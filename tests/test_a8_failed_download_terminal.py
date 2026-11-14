"""A8 — échec définitif : notification HA, pas de reprise auto, réessai manuel."""

import json
from datetime import date, timedelta

from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_a7_download_attempts import (
    NOW,
    _advance_past_backoff,
    _fictional_series,
)
from tests.test_aria2_startup import QueueDownloader
from tests.test_fill_missing import _provider_id, _Source
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link

from mediatracker.core.aria2_reconcile import maybe_reconcile_aria2, reset_startup_reconcile
from mediatracker.core.incomplete import maybe_clean_incomplete
from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.constants import MAX_ATTEMPTS
from mediatracker.core.source_poll import _blocking_download, refresh_library_claims, run_poll
from mediatracker.db.models import Download, Event, Link, Media, MediaItem, WebhookDelivery
from mediatracker.events.deliver import deliver_due
from mediatracker.providers.base import DirectLink
from mediatracker.providers.debrid.alldebrid import AllDebridError

LATER = NOW + timedelta(days=2)
LONE_URL = "https://uploady.io/file/terminal"


class _FlakyDebrid(FakeDebrid):
    def __init__(self, fail_url: str) -> None:
        super().__init__()
        self.fail_url = fail_url

    def unlock(self, url: str) -> DirectLink:
        if url == self.fail_url:
            self.seen.append(url)
            raise AllDebridError("RATE_LIMIT", "Trop de requêtes.", retryable=True)
        return super().unlock(url)


def _fail_job(client, app) -> tuple[int, FakeDebrid]:
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _fictional_series(client)
    link = _link(client, media["id"], LONE_URL, 1)
    debrid = _FlakyDebrid(LONE_URL)
    downloader = FakeDownloader()
    moment = NOW
    db = app.state.session_factory()
    try:
        row = db.get(Download, link["download_id"])
        for attempt in range(1, MAX_ATTEMPTS + 3):
            moment = _advance_past_backoff(moment, min(attempt, MAX_ATTEMPTS))
            tick(db, app.state.settings, now=moment, debrid=debrid, downloader=downloader)
            db.refresh(row)
            if row.state == "failed":
                break
        assert row.state == "failed"
        download_id = row.id
    finally:
        db.close()
    return download_id, debrid


def test_download_failed_reaches_home_assistant(client, app, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"notify_group_seconds": 0}},
        headers=csrf(client),
    )
    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "salon",
            "url": "http://homeassistant.local:8123/api/webhook/mediatracker",
            "type": "homeassistant",
            "events": ["download.failed"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    calls: list[bytes] = []

    def send(url, body, headers):
        del url, headers
        calls.append(body)
        return 200

    monkeypatch.setattr("mediatracker.events.deliver.post_request", send)
    download_id, _debrid = _fail_job(client, app)
    db = app.state.session_factory()
    try:
        assert deliver_due(db, app.state.settings, LATER) == 1
        payload = json.loads(calls[0])
        assert payload["event"] == "download.failed"
        assert payload["title"] == "Téléchargement échoué"
        row = db.get(Download, download_id)
        failed_event = db.scalar(
            select(Event).where(Event.type == "download.failed", Event.item_id == row.media_item_id)
        )
        assert failed_event is not None
        assert failed_event.payload.get("status") == "En erreur"
        assert db.query(WebhookDelivery).filter(WebhookDelivery.status == "sent").count() == 1
        assert row.state == "failed"
    finally:
        db.close()


def test_worker_does_not_resume_failed_download(client, app):
    download_id, debrid = _fail_job(client, app)
    seen_before = len(debrid.seen)
    db = app.state.session_factory()
    try:
        row = db.get(Download, download_id)
        failed_at = row.finished_at
        tick(db, app.state.settings, now=LATER, debrid=debrid, downloader=FakeDownloader())
        db.refresh(row)
        assert row.state == "failed"
        assert row.finished_at == failed_at
        assert row.next_retry_at is None
        assert len(debrid.seen) == seen_before
    finally:
        db.close()


def test_fill_missing_does_not_queue_while_failed(client, app):
    download_id, _debrid = _fail_job(client, app)
    db = app.state.session_factory()
    try:
        download = db.get(Download, download_id)
        item = db.get(MediaItem, download.media_item_id)
        media = db.get(Media, item.media_id)
        item.state = "missing"
        item.downloaded = False
        item.watched = False
        item.air_date = date(2020, 1, 1)
        media.followed = True
        media.new_episode_policy = "auto"
        db.commit()
        assert _blocking_download(db, item.id) is True
        report = run_poll(
            db,
            media,
            _provider_id(db),
            _Source(episode=item.episode or 1),
            app.state.settings,
            now=LATER,
            force=True,
        )
        assert report.queued == 0
        assert (
            db.scalar(
                select(Download.id).where(Download.media_item_id == item.id, Download.state == "pending")
            )
            is None
        )
    finally:
        db.close()


def test_missing_reconcile_does_not_resume_failed(client, app):
    download_id, _debrid = _fail_job(client, app)
    db = app.state.session_factory()
    try:
        download = db.get(Download, download_id)
        item = db.get(MediaItem, download.media_item_id)
        media = db.get(Media, item.media_id)
        item.downloaded = False
        item.file_path = None
        item.state = "missing"
        db.commit()
        refresh_library_claims(db, media, [item], app.state.settings, LATER)
        db.refresh(download)
        assert download.state == "failed"
        assert (
            db.scalar(
                select(Download.id).where(Download.media_item_id == item.id, Download.state == "pending")
            )
            is None
        )
    finally:
        db.close()


def test_startup_reconcile_leaves_failed_download(client, app):
    download_id, _debrid = _fail_job(client, app)
    reset_startup_reconcile()
    db = app.state.session_factory()
    try:
        download = db.get(Download, download_id)
        download.handle = "gid-stale"
        download.next_retry_at = NOW
        db.commit()
        maybe_reconcile_aria2(db, app.state.settings, now=LATER, downloader=QueueDownloader([]))
        db.refresh(download)
        assert download.state == "failed"
        assert download.handle == "gid-stale"
    finally:
        db.close()


def test_incomplete_cleanup_does_not_resume_failed(client, app):
    download_id, _debrid = _fail_job(client, app)
    db = app.state.session_factory()
    try:
        download = db.get(Download, download_id)
        maybe_clean_incomplete(db, app.state.settings, now=LATER)
        db.refresh(download)
        assert download.state == "failed"
    finally:
        db.close()


def test_retry_resets_counters_and_dead_hosters(client, app):
    download_id, debrid = _fail_job(client, app)
    db = app.state.session_factory()
    try:
        row = db.get(Download, download_id)
        assert row.state == "failed"
        assert db.query(Link).filter(Link.dead.is_(True)).count() >= 1
        assert row.skipped_link_ids
    finally:
        db.close()

    retried = client.post(f"/api/v1/downloads/{download_id}/retry", headers=csrf(client))
    assert retried.status_code == 200, retried.text
    body = retried.json()
    assert body["state"] == "pending"
    assert body["attempts"] == 0

    db = app.state.session_factory()
    try:
        row = db.get(Download, download_id)
        assert row.skipped_link_ids == []
        assert row.attempt_started_at is None
        assert all(not link.dead for link in db.query(Link).filter(Link.media_item_id == row.media_item_id))
        debrid.seen.clear()
        tick(db, app.state.settings, now=LATER, debrid=debrid, downloader=FakeDownloader())
        assert debrid.seen == [LONE_URL]
    finally:
        db.close()

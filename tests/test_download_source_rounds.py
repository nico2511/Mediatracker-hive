"""Tours de sources vides : backoff et anti-boucle (option C)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from tests.conftest import connect_alldebrid_for_tests, login
from tests.test_download_profile_repli_fallback import _run_begin
from tests.test_pipeline import FakeDebrid, FakeDownloader

from mediatracker.core.pipeline import queue as download_queue
from mediatracker.core.pipeline.constants import BACKOFFS, REDISCOVER_MESSAGE
from mediatracker.core.pipeline.source_rounds import (
    DAILY_SOURCE_ROUND_GAP,
    MAX_SOURCE_ROUND_TOURS,
    source_round_delay,
)
from mediatracker.core.source_poll import _requeue_failed_download
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.db.session import commit_retry

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _pending_job(db) -> tuple[Download, MediaItem, Media]:
    media = Media(type="series", title="Série exemple", year=2026, source_order="mock_a,mock_b")
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=2, episode=5, state="missing")
    db.add(item)
    db.flush()
    row = Download(media_item_id=item.id, state="pending")
    db.add(row)
    commit_retry(db)
    return row, item, media


def test_source_round_delay_matches_backoffs():
    assert source_round_delay(1) == BACKOFFS[0]
    assert source_round_delay(4) == BACKOFFS[3]
    assert source_round_delay(5) == DAILY_SOURCE_ROUND_GAP


def test_empty_pass_schedules_backoff_instead_of_failed(client, app, db, settings, monkeypatch):
    """Reproduit la boucle : sans pause, un second passage immédiat serait possible."""
    connect_alldebrid_for_tests(app)
    login(client)
    row, _item, _media = _pending_job(db)

    fast_polls: list[int] = []
    monkeypatch.setattr(
        "mediatracker.core.pipeline.source_rounds._fast_source_poll",
        lambda *_a, **_k: fast_polls.append(1),
    )
    monkeypatch.setattr(
        "mediatracker.core.pipeline.source_rounds._throttle_poll",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(download_queue, "wawacity_had_no_stored_links", lambda _row: True)

    outcome, *_rest = _run_begin(db, row, NOW, settings, monkeypatch)
    assert outcome == "waiting"
    assert row.state == "pending"
    assert row.source_rounds == 1
    assert row.next_retry_at == NOW + BACKOFFS[0]
    assert row.error == REDISCOVER_MESSAGE
    assert fast_polls == [1]

    begin_calls: list[int] = []

    def track_begin(*_a, **_k):
        begin_calls.append(1)
        return "waiting"

    monkeypatch.setattr(download_queue, "_begin", track_begin)
    monkeypatch.setattr(download_queue, "load_known_files", lambda *_a, **_k: ([], False))

    download_queue._start_pending(
        db, settings, NOW + timedelta(seconds=30), None, FakeDebrid(), None, FakeDownloader(), None
    )
    assert begin_calls == []

    begin_calls.clear()
    download_queue._start_pending(
        db, settings, NOW + BACKOFFS[0], None, FakeDebrid(), None, FakeDownloader(), None
    )
    assert begin_calls == [1]


def test_requeue_failed_download_respects_backoff_without_new_link(client, app, db):
    del client
    media = Media(type="series", title="Requeue Backoff", year=2026)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="available")
    db.add(item)
    db.flush()
    failed = Download(
        media_item_id=item.id,
        state="failed",
        error_code="NO_LINK",
        error="test",
        source_rounds=2,
        finished_at=NOW,
        created_at=NOW - timedelta(hours=1),
    )
    db.add(failed)
    commit_retry(db)
    assert _requeue_failed_download(db, item.id, NOW + timedelta(minutes=10), None, fresh_links=False) is None
    assert _requeue_failed_download(db, item.id, NOW + BACKOFFS[1], None, fresh_links=False) is not None


def test_requeue_failed_download_resets_rounds_with_fresh_link(client, app, db):
    del client
    media = Media(type="series", title="Requeue Neuf", year=2026)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=1, episode=2, state="available")
    db.add(item)
    db.flush()
    failed = Download(
        media_item_id=item.id,
        state="failed",
        error_code="NO_LINK",
        error="test",
        source_rounds=3,
        finished_at=NOW,
        created_at=NOW - timedelta(hours=2),
    )
    db.add(failed)
    commit_retry(db)
    row = _requeue_failed_download(db, item.id, NOW, None, fresh_links=True)
    assert row is not None
    assert row.source_rounds == 0


def test_at_cap_daily_backoff(client, app, db, settings, monkeypatch):
    connect_alldebrid_for_tests(app)
    login(client)
    row, _item, _media = _pending_job(db)
    row.source_rounds = MAX_SOURCE_ROUND_TOURS
    commit_retry(db)
    monkeypatch.setattr(download_queue, "_fast_source_poll", lambda *_a, **_k: None)
    monkeypatch.setattr(download_queue, "_throttle_poll", lambda *_a, **_k: None)
    _run_begin(db, row, NOW, settings, monkeypatch)
    assert row.source_rounds == MAX_SOURCE_ROUND_TOURS + 1
    assert row.next_retry_at == NOW + DAILY_SOURCE_ROUND_GAP


def test_retry_job_resets_source_rounds(client, app, db, settings):
    del client
    media = Media(type="series", title="Retry reset", year=2026)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=1, episode=3, state="available")
    db.add(item)
    db.flush()
    row = Download(media_item_id=item.id, state="failed", source_rounds=2, error_code="NO_LINK")
    db.add(row)
    commit_retry(db)
    download_queue.retry_job(db, row, settings)
    assert row.source_rounds == 0


def test_force_search_resets_rounds(client, app, settings):
    login(client)
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Forcer recherche", year=2026)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=8, state="missing")
        db.add(item)
        db.flush()
        row = Download(
            media_item_id=item.id,
            state="pending",
            source_rounds=3,
            next_retry_at=NOW + timedelta(hours=5),
            error=REDISCOVER_MESSAGE,
        )
        db.add(row)
        commit_retry(db)
        dl_id = row.id
    finally:
        db.close()

    from tests.conftest import csrf

    login(client)
    resp = client.post(f"/api/v1/downloads/{dl_id}/force-search", headers=csrf(client))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["source_rounds"] == 0
    assert body["state"] == "pending"
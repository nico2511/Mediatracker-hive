"""A7 — trois échecs par hébergeur, bascule, puis erreur définitive."""

from datetime import datetime, timedelta, timezone

from tests.conftest import csrf, link_create_json, login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link

from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.constants import BACKOFFS, MAX_ATTEMPTS
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS, DEFAULT_HOSTER_PREFERENCE
from mediatracker.db.models import Download, Event, Link
from mediatracker.providers.base import DirectLink
from mediatracker.providers.debrid.alldebrid import AllDebridError

NOW = datetime(2099, 3, 1, 12, tzinfo=timezone.utc)


class _FlakyDebrid(FakeDebrid):
    """Trois erreurs réessayables sur la même URL, puis débridage normal."""

    def __init__(self, fail_url: str, *, failures: int = MAX_ATTEMPTS) -> None:
        super().__init__()
        self.fail_url = fail_url
        self.failures_left = failures

    def unlock(self, url: str) -> DirectLink:
        if url == self.fail_url and self.failures_left > 0:
            self.failures_left -= 1
            self.seen.append(url)
            raise AllDebridError("RATE_LIMIT", "Trop de requêtes.", retryable=True)
        return super().unlock(url)


def _advance_past_backoff(moment: datetime, attempts: int) -> datetime:
    if attempts <= 0:
        return moment
    return moment + BACKOFFS[min(attempts - 1, len(BACKOFFS) - 1)] + timedelta(seconds=1)


def _fictional_series(client) -> dict:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Série Fictive", "year": 2099},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()


def test_max_attempts_is_three():
    assert MAX_ATTEMPTS == 3


def test_default_hoster_order_excludes_forbidden_names():
    for name in BLOCKED_SOURCE_HOSTERS:
        assert name not in DEFAULT_HOSTER_PREFERENCE
    assert DEFAULT_HOSTER_PREFERENCE[-1] == "1fichier"


def test_three_unlock_failures_then_switch_resets_attempts(client, app):
    login(client)
    _enable_debrid(app)
    media = _fictional_series(client)
    rg_url = "https://rapidgator.net/file/alpha"
    first = _link(client, media["id"], rg_url, 1)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://turbobit.net/file/beta.html",
            season=1,
            episode=1,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = _FlakyDebrid(rg_url)
    downloader = FakeDownloader()
    moment = NOW
    db = app.state.session_factory()
    try:
        row = db.get(Download, first["download_id"])
        for attempt in range(1, MAX_ATTEMPTS + 4):
            moment = _advance_past_backoff(moment, min(attempt, MAX_ATTEMPTS))
            tick(db, app.state.settings, now=moment, debrid=debrid, downloader=downloader)
            db.refresh(row)
            if any("turbobit" in url for url in debrid.seen):
                break
        assert db.get(Link, first["id"]).dead is True
        assert row.attempts == 0
        assert any("turbobit" in url for url in debrid.seen)
        switches = db.query(Event).filter(Event.type == "download.hoster_switched").all()
        assert len(switches) >= 1
        assert switches[-1].payload.get("from_hoster") == "rapidgator"
    finally:
        db.close()
    for url in debrid.seen:
        lowered = url.casefold()
        assert "1fichier" not in lowered
        assert "uptobox" not in lowered


def test_all_hosters_exhausted_stays_failed_without_auto_retry(client, app):
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _fictional_series(client)
    lone_url = "https://uploady.io/file/lone"
    link = _link(client, media["id"], lone_url, 2)
    debrid = _FlakyDebrid(lone_url)
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
        failed_at = row.finished_at
        tick(db, app.state.settings, now=moment + timedelta(days=1), debrid=debrid, downloader=downloader)
        db.refresh(row)
        assert row.state == "failed"
        assert row.finished_at == failed_at
        failed_event = db.query(Event).filter(Event.type == "download.failed", Event.item_id == row.media_item_id).one()
        assert failed_event.payload.get("status") == "En erreur"
        assert debrid.seen == [lone_url] * MAX_ATTEMPTS
    finally:
        db.close()


def test_unavailable_single_hoster_fails_without_wawacity_poll(client, app, monkeypatch):
    """Hébergeur seul en panne : erreur finale, pas de nouvelle requête source."""
    login(client)
    _enable_debrid(app)
    media = _fictional_series(client)
    link = _link(client, media["id"], "https://dailyuploads.net/file/solo", 3)
    polls = {"count": 0}

    def _forbid_poll(*args, **kwargs):
        polls["count"] += 1
        raise AssertionError("poll interdit")

    monkeypatch.setattr("mediatracker.core.pipeline.jobs._rediscover", _forbid_poll)
    debrid = FakeDebrid()
    debrid.mode = "unavailable"
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.state == "failed"
        assert polls["count"] == 0
        assert db.query(Event).filter(Event.type == "download.failed").count() == 1
    finally:
        db.close()

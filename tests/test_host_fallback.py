"""A down hoster is skipped at once. A transfer stuck at 0 % does not sit overnight."""

from datetime import datetime, timedelta, timezone

from tests.conftest import csrf, link_create_json, login
from tests.test_comet import _CacheDebrid, _series_demo, _stub_source
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.constants import french_error, host_down
from mediatracker.core.selection import DEFAULT_HOSTER_PREFERENCE
from mediatracker.db.models import Download, Link
from mediatracker.providers.base import DownloadStatus

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)
MAINTENANCE = "Host under maintenance or not available"


def test_default_order_is_the_four_hosters_with_1fichier_last():
    assert DEFAULT_HOSTER_PREFERENCE == (
        "rapidgator",
        "turbobit",
        "uploady",
        "dailyuploads",
        "1fichier",
    )
    assert "nitroflare" not in DEFAULT_HOSTER_PREFERENCE
    assert DEFAULT_HOSTER_PREFERENCE[-1] == "1fichier"


def test_maintenance_message_is_a_down_host_in_french():
    assert host_down("LINK_HOST_UNAVAILABLE", MAINTENANCE)
    assert host_down("ARIA2", MAINTENANCE)
    assert host_down(None, "Host unavailable")
    assert not host_down("LINK_DOWN", "This link is not available")
    assert french_error("LINK_HOST_UNAVAILABLE", MAINTENANCE) == "Hébergeur indisponible."
    assert "maintenance" not in french_error("ARIA2", MAINTENANCE).casefold()


def test_nitroflare_outside_the_order_is_not_started(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://nitroflare.com/view/ABC", 4)
    debrid = FakeDebrid()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.state == "failed"
    finally:
        db.close()
    assert debrid.seen == []


def test_maintenance_switches_to_the_next_hoster_in_the_same_tick(client, app):
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "nitroflare,rapidgator,turbobit,uploady,dailyuploads"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _series(client)
    nitro = _link(client, media["id"], "https://nitroflare.com/view/ABC", 4)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://rapidgator.net/file/next",
            season=1,
            episode=4,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = FakeDebrid()
    debrid.mode = "nitroflare"
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, nitro["download_id"])
        assert row.state == "queued"
        assert row.attempts == 0
        assert row.error_code is None
        stored = db.get(Link, nitro["id"])
        assert stored.dead is True
    finally:
        db.close()
    assert debrid.seen[0].startswith("https://nitroflare.com/")
    assert debrid.seen[1].startswith("https://rapidgator.net/")
    assert downloader.urls == ["https://cdn.example/secret.mkv"]


def test_pending_maintenance_does_not_wait_for_the_backoff(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    nitro = _link(client, media["id"], "https://nitroflare.com/view/ABC", 4)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://rapidgator.net/file/next",
            season=1,
            episode=4,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    db = app.state.session_factory()
    try:
        row = db.get(Download, nitro["download_id"])
        row.link_id = nitro["id"]
        row.state = "pending"
        row.attempts = 4
        row.error_code = "LINK_HOST_UNAVAILABLE"
        row.error = MAINTENANCE
        row.progress = 0
        row.next_retry_at = NOW + timedelta(hours=12)
        db.commit()
    finally:
        db.close()
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, nitro["download_id"])
        assert row.state == "queued"
        assert row.attempts == 0
        buried = db.get(Link, nitro["id"])
        assert buried.dead is True
    finally:
        db.close()
    assert debrid.seen == ["https://rapidgator.net/file/next"]
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["state"] == "queued"
    assert listed["hoster"] == "rapidgator"
    assert listed["error"] is None


def test_maintenance_falls_back_to_a_cached_comet_magnet(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"hoster_preference": "nitroflare,rapidgator,turbobit,uploady,dailyuploads"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _series_demo(client, app)
    link = _link(client, media["id"], "https://nitroflare.com/view/ABC", 1)
    source = _stub_source(monkeypatch)
    debrid = _CacheDebrid()
    debrid.mode = "unavailable"
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.error_code is None
    finally:
        db.close()
    assert source.calls == 1
    assert debrid.uploaded
    assert downloader.urls == ["https://cdn.example/comet.mkv"]
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["source"] == "Comet"
    assert listed["state"] == "queued"


def test_no_remaining_hoster_or_comet_is_an_error_with_retry(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 4)
    debrid = FakeDebrid()
    debrid.mode = "unavailable"
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.state == "failed"
        assert row.error_code == "LINK_HOST_UNAVAILABLE"
    finally:
        db.close()
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["state"] == "failed"
    assert listed["error"] == "Hébergeur indisponible."
    retried = client.post(f"/api/v1/downloads/{link['download_id']}/retry", headers=csrf(client))
    assert retried.status_code == 200, retried.text
    assert retried.json()["state"] == "pending"


def test_zero_progress_timeout_hands_off_then_a_fresh_attempt_is_kept(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    nitro = _link(client, media["id"], "https://nitroflare.com/view/ABC", 4)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://rapidgator.net/file/next",
            season=1,
            episode=4,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    db = app.state.session_factory()
    try:
        row = db.get(Download, nitro["download_id"])
        row.link_id = nitro["id"]
        row.state = "queued"
        row.handle = "stuck"
        row.progress = 0
        row.done_bytes = 0
        row.attempt_started_at = NOW - timedelta(minutes=31)
        row.created_at = NOW - timedelta(days=1)
        db.commit()
    finally:
        db.close()

    class Idle(FakeDownloader):
        def status(self, handle: str) -> DownloadStatus:
            del handle
            return DownloadStatus(state="queued", done_bytes=0, total_bytes=None, speed=None)

    downloader = Idle()
    debrid = FakeDebrid()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, nitro["download_id"])
        assert row.state == "queued"
        assert row.handle != "stuck"
        assert row.attempts == 0
        assert db.get(Link, nitro["id"]).dead is True
        fresh = row.attempt_started_at
        assert fresh is not None
        assert NOW - _aware(fresh) < timedelta(minutes=30)
    finally:
        db.close()
    assert "stuck" in downloader.removed
    assert debrid.seen == ["https://rapidgator.net/file/next"]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

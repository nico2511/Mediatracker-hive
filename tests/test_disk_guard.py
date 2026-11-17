"""Below the free-space threshold, new downloads stay pending and a notice is recorded once."""

from datetime import datetime, timezone

from tests.conftest import csrf, login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.disk import GIB, LAUNCH_SIZE_MARGIN, LAUNCH_UNKNOWN_SIZE_FALLBACK, launch_free_required
from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, Event, Setting
from mediatracker.providers.base import DirectLink


class PausingDownloader(FakeDownloader):
    def __init__(self) -> None:
        super().__init__()
        self.paused: list[str] = []

    def pause(self, handle: str) -> None:
        self.paused.append(handle)


def test_disk_guard_pauses_and_notifies_once(client, app):
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"disk_min_free_gb": 5}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = PausingDownloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=now,
            debrid=debrid,
            downloader=downloader,
            disk_free=lambda _path: 1024,
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "pending"
        assert row.error_code == "DISK_LOW"
        assert downloader.urls == []
        notices = db.query(Event).filter(Event.type == "disk.low").all()
        assert len(notices) == 1
        tick(
            db,
            app.state.settings,
            now=now,
            debrid=debrid,
            downloader=downloader,
            disk_free=lambda _path: 1024,
        )
        assert db.query(Event).filter(Event.type == "disk.low").count() == 1
        flag = db.get(Setting, "system.disk_low")
        assert flag is not None and flag.value == "1"
    finally:
        db.close()


def test_launch_free_required_uses_disk_min_free_gb(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"disk_min_free_gb": 3}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    try:
        file_size = 2 * GIB
        assert launch_free_required(db, app.state.settings, None) == 3 * GIB
        assert launch_free_required(db, app.state.settings, file_size) == file_size + LAUNCH_SIZE_MARGIN + 3 * GIB
    finally:
        db.close()


def test_launch_free_required_zero_disables_reserve(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"disk_min_free_gb": 0}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    try:
        assert launch_free_required(db, app.state.settings, None) == LAUNCH_UNKNOWN_SIZE_FALLBACK
        assert launch_free_required(db, app.state.settings, 1_000) == 1_000 + LAUNCH_SIZE_MARGIN
    finally:
        db.close()


class LargeFileDebrid(FakeDebrid):
    def unlock(self, url: str) -> DirectLink:
        return DirectLink(
            url="https://cdn.example/large.mkv",
            filename="Segment.mkv",
            size=2 * GIB,
        )


def test_launch_waits_for_reserve_plus_file_size(client, app):
    login(client)
    _enable_debrid(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"disk_min_free_gb": 3, "max_concurrent_downloads": 5}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = LargeFileDebrid()
    downloader = FakeDownloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=now,
            debrid=debrid,
            downloader=downloader,
            disk_free=lambda _path: 4 * GIB,
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "pending"
        assert row.error_code == "DISK_FULL"
        assert downloader.urls == []
    finally:
        db.close()

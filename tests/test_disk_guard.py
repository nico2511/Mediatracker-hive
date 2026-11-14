"""Below the free-space threshold, new downloads stay pending and a notice is recorded once."""

from datetime import datetime, timezone

from tests.conftest import csrf, login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, Event, Setting


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

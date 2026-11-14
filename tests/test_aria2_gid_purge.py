"""After a GID leaves the queue, aria2 must drop its stopped result."""

from datetime import datetime, timezone

from tests.conftest import login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.pipeline import cancel_job, tick
from mediatracker.db.models import Download
from mediatracker.providers.base import DownloadStatus

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def test_cancel_forgets_the_aria2_gid():
    downloader = FakeDownloader()
    row = Download(id=3, media_item_id=1, state="downloading", handle="gid-cancel")
    cancel_job(None, row, downloader, now=NOW)  # type: ignore[arg-type]
    assert row.state == "cancelled"
    assert downloader.removed == ["gid-cancel"]


def test_error_path_forgets_the_aria2_gid(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        gid = row.handle
        assert gid
        downloader._status[gid] = DownloadStatus(
            state="error",
            done_bytes=0,
            total_bytes=32,
            error="aria2 a échoué.",
        )
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        assert gid in downloader.removed
    finally:
        db.close()


def test_import_forgets_the_aria2_gid(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        gid = row.handle
        downloader.complete()
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        db.refresh(row)
        assert row.state == "done"
        assert downloader.removed == [gid]
        assert row.handle is None
    finally:
        db.close()


def test_import_still_finishes_when_aria2_purge_fails(client, app):
    class FailingDownloader(FakeDownloader):
        def remove(self, handle: str, delete_files: bool = False) -> None:
            raise RuntimeError("aria2 down")

    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = FailingDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        downloader.complete()
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert row.state == "done"
    finally:
        db.close()

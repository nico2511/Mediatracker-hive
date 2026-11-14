"""Startup reconcile between aria2 and the download queue."""

from datetime import datetime, timedelta, timezone

from tests.conftest import login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.aria2_reconcile import maybe_reconcile_aria2, reset_startup_reconcile
from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.providers.base import DownloadStatus, Health
from mediatracker.providers.downloaders.aria2 import Aria2Error, Aria2QueueEntry

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


class QueueDownloader(FakeDownloader):
    def __init__(self, entries: list[Aria2QueueEntry]) -> None:
        super().__init__()
        self.entries = list(entries)
        self.purged: list[tuple[str, bool]] = []
        self.saved = 0

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=NOW)

    def list_queue(self) -> list[Aria2QueueEntry]:
        return list(self.entries)

    def purge_orphan(self, gid: str, *, active: bool) -> None:
        self.purged.append((gid, active))
        self.entries = [entry for entry in self.entries if entry.gid != gid]

    def save_session(self) -> None:
        self.saved += 1


def _seed_active_download(db, *, handle: str | None, filename: str, state: str = "downloading") -> Download:
    media = Media(type="series", title="Exemple", year=2024, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="downloading")
    db.add(item)
    db.flush()
    row = Download(
        media_item_id=item.id,
        state=state,
        handle=handle,
        filename=filename,
        progress=10,
        done_bytes=8,
    )
    db.add(row)
    db.commit()
    return row


class FailingListQueueDownloader(QueueDownloader):
    def __init__(self) -> None:
        super().__init__([])
        self.list_calls = 0

    def list_queue(self) -> list[Aria2QueueEntry]:
        self.list_calls += 1
        raise Aria2Error("RPC", "The parameter at 0 is required but missing")


def test_startup_list_queue_failure_is_throttled(client, app, caplog):
    login(client)
    reset_startup_reconcile()
    downloader = FailingListQueueDownloader()
    db = app.state.session_factory()
    try:
        with caplog.at_level("WARNING", logger="mediatracker.core.aria2_reconcile"):
            maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
            maybe_reconcile_aria2(db, app.state.settings, now=NOW + timedelta(seconds=30), downloader=downloader)
            maybe_reconcile_aria2(
                db,
                app.state.settings,
                now=NOW + timedelta(minutes=11),
                downloader=downloader,
            )
    finally:
        db.close()
    assert downloader.list_calls == 2
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "Impossible de lire la file aria2 au démarrage" in warnings[0].message
    assert caplog.text.count("Traceback") == 0


def test_startup_purges_orphan_stopped_errors(client, app):
    login(client)
    reset_startup_reconcile()
    downloader = QueueDownloader(
        [
            Aria2QueueEntry("gid-stale", "error", "Orphelin.mkv"),
            Aria2QueueEntry("gid-live", "active", "Actif.mkv"),
        ]
    )
    db = app.state.session_factory()
    try:
        _seed_active_download(db, handle="gid-live", filename="Actif.mkv")
        maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
    finally:
        db.close()
    assert ("gid-stale", False) in downloader.purged
    assert ("gid-live", True) not in downloader.purged
    assert downloader.saved == 1


def test_startup_requeues_db_job_without_living_gid(client, app):
    login(client)
    reset_startup_reconcile()
    downloader = QueueDownloader([])
    db = app.state.session_factory()
    try:
        row = _seed_active_download(db, handle="gid-gone", filename="Manquant.mkv")
        maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
        assert row.handle is None
        assert row.state == "pending"
        due = row.next_retry_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        assert due == NOW
    finally:
        db.close()


def test_startup_does_not_duplicate_active_job(client, app):
    login(client)
    _enable_debrid(app)
    reset_startup_reconcile()
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    downloader = QueueDownloader([Aria2QueueEntry("gid-live", "active", "Episode.mkv")])
    db = app.state.session_factory()
    try:
        row = db.get(Download, link["download_id"])
        row.state = "downloading"
        row.handle = "gid-live"
        row.filename = "Episode.mkv"
        db.commit()
        maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
        db.refresh(row)
        assert row.handle == "gid-live"
        assert downloader.purged == []
        tick(db, app.state.settings, now=NOW, debrid=FakeDebrid(), downloader=downloader)
        assert len(downloader.urls) == 0
    finally:
        db.close()


class ExpiredDownloader(FakeDownloader):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def status(self, handle: str) -> DownloadStatus:
        self.calls += 1
        if self.calls == 1:
            return DownloadStatus(
                state="error",
                done_bytes=0,
                total_bytes=32,
                error="3: Resource not found",
            )
        return super().status(handle)


def test_expired_debrid_link_triggers_fresh_unlock(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    monkeypatch.setattr("mediatracker.core.pipeline._direct_reject_reason", lambda *args, **kwargs: None)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/expired", 1)
    debrid = FakeDebrid()
    downloader = ExpiredDownloader()
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        assert len(debrid.seen) == 1
        assert len(downloader.urls) == 1
        first_url = downloader.urls[0]
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert row.error_code == "ARIA2_REUNLOCK"
        due = row.next_retry_at
        if due is not None and due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        moment = (due or NOW) + timedelta(seconds=1)
        tick(db, app.state.settings, now=moment, debrid=debrid, downloader=downloader)
        assert len(debrid.seen) == 2
        assert len(downloader.urls) == 2
        assert debrid.seen[0] == debrid.seen[1]
        assert first_url.startswith("https://cdn.example/")
    finally:
        db.close()

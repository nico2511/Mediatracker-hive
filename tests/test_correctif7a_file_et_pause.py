"""Correctif 7a : SSE queue_held, reprise aria2 au redémarrage, libellés UI, HTTP 413."""

from pathlib import Path

from tests.conftest import csrf, login
from tests.test_aria2_startup import NOW, QueueDownloader, _seed_active_download
from tests.test_pipeline import FakeDownloader

from mediatracker.core.aria2_reconcile import maybe_reconcile_aria2, reset_startup_reconcile
from mediatracker.core.queue_hold import set_downloads_held
from mediatracker.providers.downloaders.aria2 import Aria2QueueEntry

ROOT = Path(__file__).resolve().parents[1]
API_TS = ROOT / "frontend" / "src" / "api.ts"
DOWNLOADS = ROOT / "frontend" / "src" / "views" / "DownloadsView.vue"
QUEUE_HINT = ROOT / "frontend" / "src" / "downloadsQueueHint.ts"
IMPORT_VIEW = ROOT / "frontend" / "src" / "views" / "LibraryImportHistoryView.vue"


class ResumingDownloader(QueueDownloader):
    def __init__(self, entries: list[Aria2QueueEntry]) -> None:
        super().__init__(entries)
        self.resumed: list[str] = []

    def resume(self, gid: str) -> None:
        self.resumed.append(gid)


def test_progress_snapshot_includes_queue_held(client, app):
    login(client)
    paused = client.post("/api/v1/downloads/pause", headers=csrf(client))
    assert paused.status_code == 200, paused.text
    from mediatracker.api.v1.events import progress_snapshot_payload

    db = app.state.session_factory()
    try:
        payload = progress_snapshot_payload(db)
    finally:
        db.close()
    assert payload["queue_held"] is True
    assert payload["queue_paused"] is True


def test_startup_unpauses_matching_paused_handles(client, app):
    login(client)
    reset_startup_reconcile()
    downloader = ResumingDownloader([Aria2QueueEntry("gid-paused", "paused", "Episode Démo.mkv")])
    db = app.state.session_factory()
    try:
        _seed_active_download(db, handle="gid-paused", filename="Episode Démo.mkv", state="queued")
        maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
    finally:
        db.close()
    assert downloader.resumed == ["gid-paused"]


def test_startup_skips_unpause_when_queue_held(client, app):
    login(client)
    reset_startup_reconcile()
    downloader = ResumingDownloader([Aria2QueueEntry("gid-paused", "paused", "Episode Démo.mkv")])
    db = app.state.session_factory()
    try:
        _seed_active_download(db, handle="gid-paused", filename="Episode Démo.mkv", state="queued")
        set_downloads_held(db, True, downloader=FakeDownloader())
        maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
    finally:
        db.close()
    assert downloader.resumed == []


def test_unknown_paused_gid_does_not_break_unpause(client, app):
    login(client)
    reset_startup_reconcile()
    downloader = ResumingDownloader(
        [
            Aria2QueueEntry("gid-unknown", "paused", "Orphelin.mkv"),
            Aria2QueueEntry("gid-live", "paused", "Actif.mkv"),
        ]
    )
    db = app.state.session_factory()
    try:
        _seed_active_download(db, handle="gid-live", filename="Actif.mkv", state="queued")
        maybe_reconcile_aria2(db, app.state.settings, now=NOW, downloader=downloader)
    finally:
        db.close()
    assert downloader.resumed == ["gid-live"]


def test_frontend_maps_http_413():
    text = API_TS.read_text(encoding="utf-8")
    assert "413:" in text
    assert "proxy inverse" in text


def test_downloads_queue_hint_copy():
    hint = QUEUE_HINT.read_text(encoding="utf-8")
    view = DOWNLOADS.read_text(encoding="utf-8")
    assert "En attente de débridage" in hint
    assert "En pause dans aria2" in hint
    assert "démarre après un job actif" in hint
    assert "downloadQueueHint" in view
    assert "runningJobs.value.length > 0" in view


def test_import_view_surfaces_413_limits():
    text = IMPORT_VIEW.read_text(encoding="utf-8")
    assert "JELLYFIN_HISTORY_UPLOAD_MAX_BYTES" in text
    assert "client_max_body_size" in text
    assert "HTTP ${err.status}" in text

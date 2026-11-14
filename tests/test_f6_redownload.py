"""F6: retéléchargement fichier absent, anti-doublon AllDebrid contourné si forcé ou absent."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from tests.conftest import csrf, login, profile_compliant_db_link_fields

from mediatracker.core.forget import TRACE_FILE_ABSENT, forget_local_file
from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.constants import REDISCOVER_RESERVED
from mediatracker.core.precheck import DUPLICATE_IGNORED, REDOWNLOAD_STARTED
from mediatracker.db.models import Download, Event, Link, Media, MediaItem, Provider
from mediatracker.providers.base import DirectLink, DownloadStatus, Health
from mediatracker.providers.debrid.alldebrid import LinkInspection

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


class _Debrid:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.known: list = []

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=NOW)

    def magnet_cache(self, magnets: list[str]) -> list:
        self.calls.append(("magnet", tuple(magnets)))
        return []

    def known_files(self) -> list:
        self.calls.append(("known",))
        return list(self.known)

    def inspect_link(self, url: str) -> LinkInspection:
        self.calls.append(("inspect", url))
        return LinkInspection(
            link=url,
            online=True,
            filename="Episode.mkv",
            size=32,
            code=None,
            message=None,
        )

    def resolve(self, url: str) -> list[str]:
        return [url]

    def unlock(self, url: str) -> DirectLink:
        self.calls.append(("unlock", url))
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)


class _Downloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def add(self, url: str, dest_dir: Path, filename: str | None, headers=None, extra_options=None) -> str:
        del headers, extra_options
        self.urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / (filename or "video.mkv")).write_bytes(b"x" * 32)
        return "gid-f6"

    def status(self, handle: str) -> DownloadStatus:
        del handle
        return DownloadStatus(state="downloading", done_bytes=1, total_bytes=32, speed=1)

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del handle, delete_files


def _enable_debrid(app) -> None:
    from tests.conftest import connect_alldebrid_for_tests

    connect_alldebrid_for_tests(app)


def _series(client, title: str = "Brume Lac") -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": title, "year": 2026, "tmdb_id": 66001},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 3, "title": "Quai", "air_date": "2026-01-08"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]


def _rapid_link(db, item_id: int, url: str = "https://rapidgator.net/file/brume-lac") -> None:
    provider = db.query(Provider).filter(Provider.type == "wawacity").one()
    db.add(
        Link(
            media_item_id=item_id,
            provider_id=provider.id,
            kind="ddl_protected",
            url=url,
            hoster="rapidgator",
            external_id="brume-rg",
            priority=10,
            **profile_compliant_db_link_fields(season=1, episode=3),
        )
    )


def _mark_absent(db, media_id: int, item_id: int) -> None:
    media = db.get(Media, media_id)
    item = db.get(MediaItem, item_id)
    item.downloaded = True
    item.file_path = "/tmp/ghost/Brume.Lac.S01E03.mkv"
    forget_local_file(db, media, item, NOW)
    assert any(t.get("kind") == TRACE_FILE_ABSENT for t in (item.traces or []))


def test_forced_job_ignores_alldebrid_duplicate(client, app):
    login(client)
    _enable_debrid(app)
    _media_id, item_id = _series(client)
    db = app.state.session_factory()
    try:
        _rapid_link(db, item_id)
        db.add(Download(media_item_id=item_id, state="pending", forced=True, created_at=NOW))
        db.commit()
    finally:
        db.close()
    debrid = _Debrid()
    debrid.known.append(
        type("Known", (), {"link": "https://rapidgator.net/file/brume-lac", "filename": "Episode.mkv", "size": 32})()
    )
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=_Downloader())
        row = db.query(Download).filter(Download.media_item_id == item_id).one()
        assert row.state == "queued"
        assert row.error_code != "ALREADY_PRESENT"
    finally:
        db.close()
    assert ("unlock", "https://rapidgator.net/file/brume-lac") in debrid.calls


def test_absent_file_redownload_api_and_pipeline(client, app):
    login(client)
    _enable_debrid(app)
    media_id, item_id = _series(client, title="Rivage Nord")
    db = app.state.session_factory()
    try:
        _rapid_link(db, item_id, url="https://rapidgator.net/file/rivage-nord")
        _mark_absent(db, media_id, item_id)
        db.commit()
    finally:
        db.close()

    with patch("mediatracker.core.source_poll.run_poll") as poll:
        response = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["message"] == REDOWNLOAD_STARTED
        assert body["status"] == "started"
        assert body["download_id"] is not None
        poll.assert_not_called()

    db = app.state.session_factory()
    try:
        event = db.query(Event).filter(Event.type == "download.redownload", Event.item_id == item_id).one()
        assert event.payload["message"] == REDOWNLOAD_STARTED
        assert event.payload["status"] == "started"
    finally:
        db.close()

    debrid = _Debrid()
    debrid.known.append(
        type("Known", (), {"link": "https://rapidgator.net/file/rivage-nord", "filename": "Episode.mkv", "size": 32})()
    )
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=_Downloader())
        row = db.get(Download, body["download_id"])
        assert row.state == "queued"
        assert row.forced is True
    finally:
        db.close()
    assert ("unlock", "https://rapidgator.net/file/rivage-nord") in debrid.calls


def test_real_duplicate_still_blocks_a_normal_job(client, app):
    login(client)
    _enable_debrid(app)
    _media_id, item_id = _series(client, title="Cendre Port")
    db = app.state.session_factory()
    try:
        _rapid_link(db, item_id, url="https://rapidgator.net/file/cendre-port")
        db.add(Download(media_item_id=item_id, state="pending", forced=False, created_at=NOW))
        db.commit()
    finally:
        db.close()
    debrid = _Debrid()
    debrid.known.append(
        type("Known", (), {"link": "https://rapidgator.net/file/cendre-port", "filename": "Episode.mkv", "size": 32})()
    )
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=_Downloader())
        row = db.query(Download).filter(Download.media_item_id == item_id).one()
        assert row.state == "cancelled"
        assert row.error_code == "ALREADY_PRESENT"
    finally:
        db.close()
    assert not any(call[0] == "unlock" for call in debrid.calls)


def test_redownload_without_links_schedules_rediscover(client, app):
    login(client)
    media_id, item_id = _series(client, title="Lanterne Sud")
    db = app.state.session_factory()
    try:
        _mark_absent(db, media_id, item_id)
        db.commit()
    finally:
        db.close()

    with patch("mediatracker.core.source_poll.run_poll") as poll:
        response = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["message"] == REDOWNLOAD_STARTED
        assert body["status"] == "started"
        poll.assert_not_called()

    db = app.state.session_factory()
    try:
        event = db.query(Event).filter(Event.type == "download.redownload", Event.item_id == item_id).one()
        assert event.payload["message"] == REDOWNLOAD_STARTED
        row = db.get(Download, body["download_id"])
        assert row.state == "pending"
        assert row.forced is True
        assert row.error_code == REDISCOVER_RESERVED
    finally:
        db.close()


def test_redownload_duplicate_on_disk_is_ignored(client, app):
    login(client)
    media_id, item_id = _series(client, title="Mistral Bay")
    data = Path(app.state.settings.data_dir)
    folder = data / "media" / "Series" / "Mistral Bay (2026) [tmdbid-66001]" / "Season 01"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "Mistral Bay (2026) - S01E03.mkv"
    video.write_bytes(b"still-here")
    db = app.state.session_factory()
    try:
        _mark_absent(db, media_id, item_id)
        db.commit()
    finally:
        db.close()

    response = client.post(f"/api/v1/items/{item_id}/redownload", headers=csrf(client))
    assert response.status_code == 200, response.text
    assert response.json()["message"] == DUPLICATE_IGNORED
    assert response.json()["status"] == "duplicate_ignored"
    db = app.state.session_factory()
    try:
        assert db.query(Download).filter(Download.media_item_id == item_id).count() == 0
        event = db.query(Event).filter(Event.type == "download.redownload", Event.item_id == item_id).one()
        assert event.payload["message"] == DUPLICATE_IGNORED
    finally:
        db.close()

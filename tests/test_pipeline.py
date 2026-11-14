import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.conftest import connect_alldebrid_for_tests, csrf, link_create_json, login, post_test_link

from mediatracker.core.naming import library_destination
from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.providers.base import DirectLink, DownloadStatus, Health
from mediatracker.providers.debrid.alldebrid import AUTH_BLOCKED_MESSAGE, AllDebridError
from mediatracker.providers.downloaders.aria2 import Aria2Error


class FakeDebrid:
    def __init__(self) -> None:
        self.mode = "ok"
        self.seen: list[str] = []

    def health(self) -> Health:
        if self.mode == "blocked":
            return Health(
                status="down",
                code="AUTH_BLOCKED",
                message=AUTH_BLOCKED_MESSAGE,
                checked_at=datetime.now(timezone.utc),
            )
        return Health(status="ok", code=None, message="ok", checked_at=datetime.now(timezone.utc))

    def resolve(self, url: str) -> list[str]:
        return [url]

    def unlock(self, url: str) -> DirectLink:
        self.seen.append(url)
        if self.mode == "blocked":
            raise AllDebridError("AUTH_BLOCKED", "blocked")
        if self.mode == "unavailable":
            raise AllDebridError("LINK_HOST_UNAVAILABLE", "Hébergeur indisponible", retryable=True)
        if self.mode == "nitroflare" and "nitroflare" in url:
            raise AllDebridError(
                "LINK_HOST_UNAVAILABLE",
                "Host under maintenance or not available",
                retryable=True,
            )
        if self.mode == "dead" and "rapidgator" in url:
            raise AllDebridError("LINK_DOWN", "Lien mort", retryable=False)
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)


class FakeDownloader:
    def __init__(self) -> None:
        self.urls: list[str] = []
        self.removed: list[str] = []
        self._status: dict[str, DownloadStatus] = {}

    def add(
        self,
        url: str,
        dest_dir: Path,
        filename: str | None,
        headers=None,
        extra_options=None,
    ) -> str:
        del headers, extra_options
        self.urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (filename or "video.mkv")
        path.write_bytes(b"x" * 32)
        gid = f"g{len(self.urls)}"
        self._status[gid] = DownloadStatus(
            state="downloading",
            done_bytes=8,
            total_bytes=32,
            speed=4,
            path=str(path),
        )
        return gid

    def status(self, handle: str) -> DownloadStatus:
        return self._status[handle]

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del delete_files
        self.removed.append(handle)

    def complete(self, handle: str | None = None) -> None:
        key = handle or (f"g{len(self.urls)}" if self.urls else "g1")
        if key not in self._status and self._status:
            key = next(reversed(self._status))
        current = self._status[key]
        self._status[key] = current.model_copy(update={"state": "complete", "done_bytes": 32, "speed": None})


def _enable_debrid(app) -> None:
    connect_alldebrid_for_tests(app)


def _series(client) -> dict:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()


def _link(client, media_id: int, url: str, episode: int, *, start: bool = True) -> dict:
    return post_test_link(client, media_id, url, season=1, episode=episode, start=start)


def test_names_follow_the_jellyfin_layout(tmp_path):
    movies = tmp_path / "media" / "Films"
    series = tmp_path / "media" / "Series"
    film = library_destination(
        movies_dir=movies,
        series_dir=series,
        media_type="film",
        title="Archipel Pilote",
        year=2021,
        season=None,
        episode=None,
        ext=".mkv",
    )
    assert film == movies / "Archipel Pilote (2021)" / "Archipel Pilote (2021).mkv"
    episode = library_destination(
        movies_dir=movies,
        series_dir=series,
        media_type="series",
        title="Exemple",
        year=2024,
        season=1,
        episode=2,
        ext=".mp4",
    )
    assert episode.name == "Exemple (2024) - S01E02.mp4"
    assert episode.parent.name == "Season 01"


def test_pipeline_moves_the_file_and_forgets_the_direct_url(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.filename.endswith(".mkv")
        assert "cdn.example" not in json.dumps(row.__dict__, default=str)
        downloader.complete()
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        db.refresh(row)
        assert row.state == "done"
        dest = Path(app.state.settings.data_dir) / "media" / "Series" / "Exemple (2024)" / "Season 01"
        assert (dest / "Exemple (2024) - S01E01.mkv").is_file()
    finally:
        db.close()
    raw = Path(app.state.settings.database_url.removeprefix("sqlite:///")).read_bytes()
    assert b"cdn.example" not in raw
    assert downloader.urls == ["https://cdn.example/secret.mkv"]
    events = client.get("/api/v1/events", params={"type": "library.imported"}).json()
    assert events["items"][0]["payload"]["path"].endswith("S01E01.mkv")


def test_completed_download_uses_the_fiche_folder_not_the_release_name(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media["id"])
        row.tmdb_id = 4242
        db.commit()
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        dl_row = db.get(Download, link["download_id"])
        gid = dl_row.handle
        assert gid
        current = downloader._status[gid]
        src = Path(current.path)
        release = src.with_name("Exemple.FRENCH.DDP5.1.BraD.Wawacity.town.S01E01.mkv")
        src.rename(release)
        downloader._status[gid] = current.model_copy(
            update={"state": "complete", "done_bytes": 32, "speed": None, "path": str(release)}
        )
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        dest = (
            Path(app.state.settings.data_dir)
            / "media"
            / "Series"
            / "Exemple (2024) [tmdbid-4242]"
            / "Season 01"
            / "Exemple (2024) - S01E01.mkv"
        )
        assert dest.is_file()
        assert not release.exists()
        series = dest.parents[2]
        assert not any("Wawacity" in path.name for path in series.iterdir())
        item = db.query(MediaItem).filter(MediaItem.media_id == media["id"], MediaItem.episode == 1).one()
        assert item.original_name == release.name
        assert db.get(Download, link["download_id"]).state == "done"
    finally:
        db.close()


def test_no_stored_links_fails_with_no_link(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.queue._usable_download_sources",
        lambda db, media, settings: ["wawacity"],
    )
    media = _series(client)
    item = client.post(
        f"/api/v1/media/{media['id']}/items",
        json={"season": 1, "episode": 9, "title": "Neuf"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    created = client.post(
        "/api/v1/downloads",
        json={"media_item_id": item.json()["id"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=FakeDebrid(), downloader=FakeDownloader())
        row = db.get(Download, created.json()["id"])
        db.refresh(row)
        assert row.state == "failed"
        assert row.error_code == "NO_LINK"
    finally:
        db.close()


def test_host_unavailable_fails_when_nothing_else_can_be_tried(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://turbobit.net/file/abc.html", 3)
    debrid = FakeDebrid()
    debrid.mode = "unavailable"
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        db.refresh(row)
        assert row.state == "failed"
        assert row.error_code == "LINK_HOST_UNAVAILABLE"
        assert row.attempts == 0
    finally:
        db.close()
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["state"] == "failed"
    assert "indisponible" in listed["error"].casefold()
    raw = Path(app.state.settings.database_url.removeprefix("sqlite:///")).read_bytes()
    assert b"cdn.example" not in raw


def test_dead_link_falls_back_to_the_next_hoster(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            media["id"],
            "https://turbobit.net/file/other.html",
            season=1,
            episode=1,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = FakeDebrid()
    debrid.mode = "dead"
    downloader = FakeDownloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
    finally:
        db.close()
    assert debrid.seen[0].startswith("https://rapidgator.net/")
    assert debrid.seen[1].startswith("https://turbobit.net/")
    assert downloader.urls == ["https://cdn.example/secret.mkv"]


def test_auth_blocked_pauses_the_queue(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    first = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    second = _link(client, media["id"], "https://katfile.com/file/xyz", 2)
    debrid = FakeDebrid()
    debrid.mode = "blocked"
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, first["download_id"])
        other = db.get(Download, second["download_id"])
        db.refresh(row)
        db.refresh(other)
        assert row.error_code == "AUTH_BLOCKED"
        assert row.attempts == 0
        assert other.state == "pending"
        assert other.error_code is None
        assert len(debrid.seen) == 1
    finally:
        db.close()
    listed = client.get("/api/v1/downloads").json()
    assert listed["queue_paused"] is True
    assert "e-mail" in listed["queue_message"]
    debrid.mode = "ok"
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
    finally:
        db.close()
    assert client.get("/api/v1/downloads").json()["queue_paused"] is False


def test_idempotent_create_retry_and_sse(client, app):
    login(client)
    media = _series(client)
    item = client.post(
        f"/api/v1/media/{media['id']}/items",
        json={"season": 1, "episode": 4, "title": "Quatre"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    headers = {**csrf(client), "Idempotency-Key": "same-key"}
    body = {"media_item_id": item.json()["id"]}
    first = client.post("/api/v1/downloads", json=body, headers=headers)
    again = client.post("/api/v1/downloads", json=body, headers=headers)
    assert first.status_code == 201, first.text
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    cancelled = client.post(f"/api/v1/downloads/{first.json()['id']}/cancel", headers=csrf(client))
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    retried = client.post(f"/api/v1/downloads/{first.json()['id']}/retry", headers=csrf(client))
    assert retried.status_code == 200
    assert retried.json()["state"] == "pending"
    app.state.sse_limit = 1
    stream = client.get("/api/v1/events/stream")
    assert stream.status_code == 200
    assert stream.headers["content-type"].startswith("text/event-stream")
    assert "event: progress" in stream.text
    assert "queue_paused" in stream.text


def test_unreachable_aria2_keeps_the_handle_until_it_answers(client, app):
    login(client)
    _enable_debrid(app)
    media = _series(client)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    debrid = FakeDebrid()
    downloader = FakeDownloader()
    now = datetime(2026, 10, 17, 5, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        handle = row.handle
        assert handle

        def offline(_handle: str) -> DownloadStatus:
            raise Aria2Error("UNREACHABLE", "aria2 est injoignable.")

        downloader.status = offline  # type: ignore[method-assign]
        tick(db, app.state.settings, now=now + timedelta(minutes=1), debrid=debrid, downloader=downloader)
        db.refresh(row)
        assert row.state == "queued"
        assert row.handle == handle
        assert row.attempts == 0
        assert downloader.urls == ["https://cdn.example/secret.mkv"]

        def missing(_handle: str) -> DownloadStatus:
            raise Aria2Error("RPC", "GID is not found")

        downloader.status = missing  # type: ignore[method-assign]
        tick(db, app.state.settings, now=now + timedelta(minutes=2), debrid=debrid, downloader=downloader)
        db.refresh(row)
        assert row.state == "pending"
        assert row.handle is None
        assert row.attempts == 1
        assert downloader.urls == ["https://cdn.example/secret.mkv"]
    finally:
        db.close()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

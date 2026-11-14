"""Pre-download checks: link/infos, local and AllDebrid dedupe, magnet cache first."""

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from tests.conftest import connect_alldebrid_for_tests, csrf, link_create_json, login, post_test_link

from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, LibraryFile
from mediatracker.providers.base import DirectLink, DownloadStatus, Health
from mediatracker.providers.debrid.alldebrid import AllDebridClient, LinkInspection


def _client(handler):
    return AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None)


def test_link_infos_follows_301_and_flags_an_offline_link():
    bodies: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content.decode())
        if request.url.host == "api.alldebrid.com":
            assert request.method == "POST"
            assert request.headers["authorization"] == "Bearer test-key"
            return httpx.Response(301, headers={"location": "https://apislow.alldebrid.com/v4/link/infos"})
        assert request.url.host == "apislow.alldebrid.com"
        assert request.method == "POST"
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "infos": [
                        {
                            "link": "https://rapidgator.net/file/abc",
                            "filename": "Episode.mkv",
                            "size": 20,
                            "host": "rapidgator",
                        },
                        {
                            "link": "https://turbobit.net/file/dead.html",
                            "error": {"code": "LINK_DOWN", "message": "This link is not available"},
                        },
                    ]
                },
            },
        )

    infos = _client(handler).link_infos(
        ["https://rapidgator.net/file/abc", "https://turbobit.net/file/dead.html"]
    )
    assert [info.online for info in infos] == [True, False]
    assert infos[0].filename == "Episode.mkv"
    assert infos[1].code == "LINK_DOWN"
    assert all(request_body for request_body in bodies)
    posted = parse_qs(bodies[-1])
    assert posted["link[]"] == ["https://rapidgator.net/file/abc", "https://turbobit.net/file/dead.html"]


def test_magnet_cache_reads_instant_without_uploading():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.host == "api.alldebrid.com":
            return httpx.Response(301, headers={"location": "https://apislow.alldebrid.com/v4/magnet/instant"})
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {"magnets": [{"magnet": "magnet:?xt=urn:btih:abc", "instant": True, "name": "Show.mkv"}]},
            },
        )

    hits = _client(handler).magnet_cache(["magnet:?xt=urn:btih:abc"])
    assert hits[0].cached is True
    assert hits[0].name == "Show.mkv"
    assert all(path.endswith("/v4/magnet/instant") for path in seen)
    assert not any("upload" in path for path in seen)


class _Downloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def add(self, url: str, dest_dir: Path, filename: str | None, headers=None, extra_options=None) -> str:
        del headers, extra_options
        self.urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / (filename or "video.mkv")
        path.write_bytes(b"x" * 32)
        return "g1"

    def status(self, handle: str) -> DownloadStatus:
        del handle
        return DownloadStatus(state="downloading", done_bytes=1, total_bytes=32, speed=1)

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del handle, delete_files


class _Debrid:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.offline: set[str] = set()
        self.known: list = []

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=datetime.now(timezone.utc))

    def magnet_cache(self, magnets: list[str]) -> list:
        self.calls.append(("magnet", tuple(magnets)))
        return []

    def known_files(self) -> list:
        self.calls.append(("known",))
        return list(self.known)

    def inspect_link(self, url: str) -> LinkInspection:
        self.calls.append(("inspect", url))
        offline = url in self.offline
        return LinkInspection(
            link=url,
            online=not offline,
            filename=None if offline else "Episode.mkv",
            size=None if offline else 32,
            code="LINK_DOWN" if offline else None,
            message="down" if offline else None,
        )

    def resolve(self, url: str) -> list[str]:
        return [url]

    def unlock(self, url: str) -> DirectLink:
        self.calls.append(("unlock", url))
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)


def _enable(app) -> None:
    connect_alldebrid_for_tests(app)


def _episode(client, url: str, *, episode: int = 1, start: bool = True) -> dict:
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    if media.status_code == 409:
        media = client.get("/api/v1/media")
        media_id = media.json()["items"][0]["id"]
    else:
        assert media.status_code == 201, media.text
        media_id = media.json()["id"]
    return post_test_link(client, media_id, url, season=1, episode=episode, start=start)


def test_offline_hoster_moves_on_without_a_retry(client, app):
    login(client)
    _enable(app)
    first = _episode(client, "https://rapidgator.net/file/abc")
    second = client.post(
        "/api/v1/links",
        json=link_create_json(
            client.get("/api/v1/media").json()["items"][0]["id"],
            "https://turbobit.net/file/other.html",
            season=1,
            episode=1,
            start=False,
        ),
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    debrid = _Debrid()
    debrid.offline.add("https://rapidgator.net/file/abc")
    downloader = _Downloader()
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=downloader)
        row = db.get(Download, first["download_id"])
        db.refresh(row)
        assert row.state == "queued"
        assert row.error_code is None
    finally:
        db.close()
    assert ("unlock", "https://rapidgator.net/file/abc") not in debrid.calls
    assert ("unlock", "https://turbobit.net/file/other.html") in debrid.calls
    assert downloader.urls == ["https://cdn.example/secret.mkv"]
    listed = client.get("/api/v1/downloads").json()
    queued = next(item for item in listed["items"] if item["state"] == "queued")
    assert queued["check"] is None
    raw = Path(app.state.settings.database_url.removeprefix("sqlite:///")).read_bytes()
    assert b"cdn.example" not in raw


def test_saved_or_history_link_is_not_downloaded_twice(client, app):
    login(client)
    _enable(app)
    created = _episode(client, "https://rapidgator.net/file/abc")
    debrid = _Debrid()
    debrid.known.append(
        type("Known", (), {"link": "https://rapidgator.net/file/abc", "filename": "Episode.mkv", "size": 32})()
    )
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=_Downloader())
        row = db.get(Download, created["download_id"])
        assert row.state == "cancelled"
        assert row.error_code == "ALREADY_PRESENT"
        assert row.attempts == 0
    finally:
        db.close()
    assert not any(call[0] == "unlock" for call in debrid.calls)
    listed = client.get("/api/v1/downloads").json()
    assert listed["items"][0]["check"] is None
    assert "déjà présent" in listed["items"][0]["error"].casefold()


def test_scanner_row_blocks_a_second_download(client, app):
    login(client)
    _enable(app)
    created = _episode(client, "https://katfile.com/file/xyz")
    folder = Path(app.state.settings.data_dir) / "media" / "Series" / "Exemple (2024)" / "Season 01"
    folder.mkdir(parents=True)
    video = folder / "Exemple (2024) - S01E01.mkv"
    video.write_bytes(b"episode-bytes")
    db = app.state.session_factory()
    try:
        db.add(
            LibraryFile(
                path=str(video),
                size=video.stat().st_size,
                parsed_title="Exemple",
                parsed_year=2024,
                season=1,
                episode=1,
                media_type="series",
                media_item_id=created["media_item_id"],
                status="matched",
                confidence="high",
                seen_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
            )
        )
        db.commit()
    finally:
        db.close()
    debrid = _Debrid()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 11, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=_Downloader(),
        )
        row = db.get(Download, created["download_id"])
        assert row.state == "cancelled"
        assert row.error_code == "ALREADY_LOCAL"
    finally:
        db.close()
    assert not any(call[0] == "inspect" for call in debrid.calls)
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["check"] is None
    assert "déjà présent" in listed["error"].casefold()


def test_magnet_cache_is_consulted_before_any_hoster_link(client, app):
    login(client)
    _enable(app)
    created = _episode(client, "https://rapidgator.net/file/abc")
    media_id = client.get("/api/v1/media").json()["items"][0]["id"]
    magnet = client.post(
        "/api/v1/links",
        json=link_create_json(
            media_id,
            "magnet:?xt=urn:btih:abc",
            season=1,
            episode=1,
            start=False,
        ),
        headers=csrf(client),
    )
    assert magnet.status_code == 201, magnet.text
    debrid = _Debrid()
    downloader = _Downloader()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 11, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=downloader,
        )
        row = db.get(Download, created["download_id"])
        assert row.state == "queued"
    finally:
        db.close()
    names = [call[0] for call in debrid.calls]
    assert names.index("magnet") < names.index("inspect")
    assert names.index("inspect") < names.index("unlock")
    assert debrid.calls[names.index("magnet")][1] == ("magnet:?xt=urn:btih:abc",)
    assert downloader.urls == ["https://cdn.example/secret.mkv"]
    assert not any(url.startswith("magnet:") for url in downloader.urls)

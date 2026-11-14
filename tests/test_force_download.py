"""Manual « Forcer le téléchargement » ignores the automatic guards."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.conftest import csrf, login, profile_compliant_db_link_fields

from mediatracker.core.pipeline import tick
from mediatracker.core.pipeline.jobs import _choose_link
from mediatracker.core.selection import parse_hoster_preference
from mediatracker.db.models import Download, Event, Link, Media, MediaItem, Provider
from mediatracker.providers.base import DirectLink, DownloadStatus, Health

NOW = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)


class _Debrid:
    def __init__(self) -> None:
        self.seen: list[str] = []

    def health(self) -> Health:
        return Health(status="ok", code=None, message="ok", checked_at=NOW)

    def resolve(self, url: str) -> list[str]:
        return [url]

    def unlock(self, url: str) -> DirectLink:
        self.seen.append(url)
        return DirectLink(url="https://cdn.example/secret.mkv", filename="Episode.mkv", size=32)

    def link_infos(self, links: list[str]):
        return []

    def saved_links(self):
        return []

    def history(self):
        return []

    def magnet_cache(self, magnets: list[str]):
        return []


class _Downloader:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def add(self, url: str, dest_dir: Path, filename: str | None, headers=None, extra_options=None) -> str:
        del headers, extra_options
        self.urls.append(url)
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / (filename or "video.mkv")).write_bytes(b"x" * 32)
        return "gid-force"

    def status(self, handle: str) -> DownloadStatus:
        del handle
        return DownloadStatus(state="downloading", done_bytes=1, total_bytes=32, speed=1, path=None)

    def remove(self, handle: str, delete_files: bool = False) -> None:
        del handle, delete_files


def _enable_debrid(app) -> None:
    from tests.conftest import connect_alldebrid_for_tests

    connect_alldebrid_for_tests(app)


def _fiche(client) -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "North Harbor",
            "year": 2024,
            "tmdb_id": 4242,
            "new_episode_policy": "propose",
            "series_status": "ended",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 4, "title": "Quai", "air_date": "2024-01-08"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    return media_id, item.json()["id"]


def test_retry_does_not_reopen_a_done_job_but_force_does(client, app):
    """« Réessayer » stays limited to failures. Force queues past a done job, a watch, and the air window."""
    login(client)
    _enable_debrid(app)
    media_id, item_id = _fiche(client)
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.watched = True
        item.downloaded = False
        item.file_path = None
        done = Download(
            media_item_id=item_id,
            state="done",
            created_at=NOW - timedelta(days=40),
            finished_at=NOW - timedelta(days=40),
        )
        db.add(done)
        db.commit()
        done_id = done.id
    finally:
        db.close()

    retried = client.post(f"/api/v1/downloads/{done_id}/retry", headers=csrf(client))
    assert retried.status_code == 409, retried.text

    forced = client.post(
        f"/api/v1/media/{media_id}/force-download",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["queued"] == 1
    assert body["needs_confirm"] is False
    assert "mis en file" in body["message"]
    assert "S01E04" in body["message"]

    db = app.state.session_factory()
    try:
        rows = list(db.query(Download).filter(Download.media_item_id == item_id).all())
        assert [row.state for row in rows].count("pending") == 1
        item = db.get(MediaItem, item_id)
        assert item.watched is True
        event = db.query(Event).filter(Event.type == "download.forced", Event.item_id == item_id).one()
        assert event.payload["season"] == 1
        assert event.payload["episode"] == 4
    finally:
        db.close()

    again = client.post(
        f"/api/v1/media/{media_id}/force-download",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert again.status_code == 409, again.text
    assert "déjà en cours" in again.json()["detail"]
    listed = client.get("/api/v1/events", params={"type": "download.forced"})
    assert listed.status_code == 200, listed.text
    assert any(row["item_id"] == item_id for row in listed.json()["items"])


def test_local_file_asks_then_a_confirmed_force_ignores_already_local(client, app):
    login(client)
    _enable_debrid(app)
    media_id, item_id = _fiche(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series" / "North Harbor (2024)"
    series.mkdir(parents=True)
    video = series / "North.Harbor.S01E04.mkv"
    video.write_bytes(b"v" * 2048)
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        item.downloaded = True
        item.file_path = str(series / "absent.mkv")
        item.state = "downloaded"
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        db.add(
            Link(
                media_item_id=item_id,
                provider_id=provider.id,
                kind="ddl_protected",
                url="https://1fichier.com/hidden",
                hoster="1fichier",
                external_id="one-fichier",
                priority=1,
                **profile_compliant_db_link_fields(season=1, episode=4),
            )
        )
        db.add(
            Link(
                media_item_id=item_id,
                provider_id=provider.id,
                kind="ddl_protected",
                url="https://rapidgator.net/file/kept",
                hoster="rapidgator",
                external_id="rapid",
                priority=10,
                dead=True,
                dead_code="LINK_HOST_UNAVAILABLE",
                dead_at=NOW - timedelta(days=1),
                **profile_compliant_db_link_fields(season=1, episode=4),
            )
        )
        db.commit()
        media = db.get(Media, media_id)
        preference = parse_hoster_preference("1fichier,rapidgator")
        chosen = _choose_link(
            db,
            Download(media_item_id=item_id, state="pending"),
            item,
            preference,
            media=media,
            settings=app.state.settings,
        )
        assert chosen is not None
        assert chosen.hoster == "1fichier"
    finally:
        db.close()

    asked = client.post(
        f"/api/v1/media/{media_id}/force-download",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert asked.status_code == 200, asked.text
    body = asked.json()
    assert body["queued"] == 0
    assert body["needs_confirm"] is True
    assert body["local"][0]["path"].endswith("North.Harbor.S01E04.mkv")
    assert "Confirmez" in body["message"]
    assert "1fichier" not in body["message"]

    confirmed = client.post(
        f"/api/v1/media/{media_id}/force-download",
        json={"item_id": item_id, "confirm": True},
        headers=csrf(client),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["queued"] == 1

    debrid = _Debrid()
    downloader = _Downloader()
    db = app.state.session_factory()
    try:
        row = db.query(Download).filter(Download.media_item_id == item_id, Download.state == "pending").one()
        assert row.forced is True
        kept = db.query(Link).filter(Link.external_id == "rapid").one()
        blocked = db.query(Link).filter(Link.external_id == "one-fichier").one()
        assert kept.dead is False
        assert blocked.dead is False
        tick(db, app.state.settings, now=NOW, debrid=debrid, downloader=downloader)
        db.refresh(row)
        assert row.state != "cancelled"
        assert row.error_code != "ALREADY_LOCAL"
        assert any("rapidgator" in url for url in debrid.seen)
    finally:
        db.close()


def test_season_force_skips_a_job_already_running(client, app):
    login(client)
    _enable_debrid(app)
    media_id, first_id = _fiche(client)
    second = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 5, "title": "Suite", "air_date": "2024-02-01"},
        headers=csrf(client),
    )
    assert second.status_code == 201, second.text
    other = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 2, "episode": 1, "title": "Ailleurs", "air_date": "2025-01-01"},
        headers=csrf(client),
    )
    assert other.status_code == 201, other.text
    db = app.state.session_factory()
    try:
        db.add(Download(media_item_id=first_id, state="downloading", created_at=NOW))
        db.commit()
    finally:
        db.close()

    forced = client.post(
        f"/api/v1/media/{media_id}/force-download",
        json={"season": 1},
        headers=csrf(client),
    )
    assert forced.status_code == 200, forced.text
    body = forced.json()
    assert body["queued"] == 1
    assert body["skipped_busy"] == 1
    assert "S01E05" in body["message"]
    assert "déjà en cours" in body["message"]
    db = app.state.session_factory()
    try:
        pending = list(
            db.query(Download).filter(Download.state == "pending").all()
        )
        assert len(pending) == 1
        item = db.get(MediaItem, pending[0].media_item_id)
        assert (item.season, item.episode) == (1, 5)
    finally:
        db.close()


def test_fiche_offers_force_when_a_finished_job_hides_the_plain_download(client):
    login(client)
    media_id, item_id = _fiche(client)
    db = client.app.state.session_factory() if hasattr(client, "app") else None
    del db
    page = Path(__file__).resolve().parents[1] / "frontend" / "src" / "views" / "MediaDetailView.vue"
    source = page.read_text(encoding="utf-8")
    assert "Forcer le téléchargement" in source
    assert "Forcer la saison" in source
    assert "force-download" in source
    episodes = client.get(f"/api/v1/media/{media_id}/items").json()["seasons"][0]["episodes"]
    row = next(item for item in episodes if item["id"] == item_id)
    assert row["download_busy"] is False

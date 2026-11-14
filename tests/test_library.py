from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.pipeline import tick
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbHit, TmdbSeason, TmdbTitle
from mediatracker.library.parse import parse_path, parse_release
from mediatracker.library.scanner import maybe_scan_library, scan_library
from mediatracker.providers.base import DirectLink, DownloadStatus, Health


def test_parse_jellyfin_release_and_tmdb_tag():
    series = Path("/library/Series")
    release = parse_path(
        series / "The.Show.S01E02.1080p.WEB-DL.x264-GROUP.mkv",
        root=series,
        root_kind="series",
    )
    assert release.title == "The Show"
    assert (release.season, release.episode, release.quality) == (1, 2, "1080p")
    compact = parse_path(series / "Show S01E02.mkv", root=series, root_kind="series")
    assert compact.title == "Show"
    assert (compact.season, compact.episode) == (1, 2)
    crossed = parse_path(series / "Other Show 1x03.mkv", root=series, root_kind="series")
    assert crossed.title == "Other Show"
    assert (crossed.season, crossed.episode) == (1, 3)
    films = Path("/library/Films")
    film = parse_path(
        films / "Archipel Pilote (2021)" / "Archipel Pilote (2021) [tmdbid-900005].mkv",
        root=films,
        root_kind="film",
    )
    assert (film.title, film.year, film.tmdb_id, film.media_type) == ("Archipel Pilote", 2021, 900005, "film")
    episode = parse_path(
        series / "Exemple (2024)" / "Season 01" / "Exemple (2024) - S01E02.mkv",
        root=series,
        root_kind="series",
    )
    assert (episode.title, episode.year, episode.season, episode.episode) == ("Exemple", 2024, 1, 2)
    specials = parse_path(
        series / "Exemple (2024)" / "Season 00" / "Exemple (2024) - S00E01.mkv",
        root=series,
        root_kind="series",
    )
    assert (specials.season, specials.episode) == (0, 1)
    folder_specials = parse_path(
        series / "Exemple (2024)" / "Specials" / "E03.mkv",
        root=series,
        root_kind="series",
    )
    assert (folder_specials.title, folder_specials.season, folder_specials.episode) == ("Exemple", 0, 3)
    multi = parse_path(series / "Exemple (2024) - S01E01-E02.mkv", root=series, root_kind="series")
    assert (multi.season, multi.episode, multi.episode_end) == (1, 1, 2)
    glued = parse_path(series / "Exemple.S01E01E02.1080p.mkv", root=series, root_kind="series")
    assert (glued.episode, glued.episode_end) == (1, 2)
    collision = parse_path(
        films / "Archipel Pilote (2021)" / "Archipel Pilote.1984.1080p.mkv",
        root=films,
        root_kind="film",
    )
    assert (collision.title, collision.year) == ("Archipel Pilote", 2021)


class FakeTmdb:
    def search(self, query: str, media_type: str) -> list[TmdbHit]:
        if query == "Ambigu":
            return [
                TmdbHit(11, media_type, "Ambigu", 2020, None, None),
                TmdbHit(12, media_type, "Ambigu", 2020, None, None),
            ]
        if query == "Demain":
            return [TmdbHit(99, media_type, "Demain", 2026, None, None)]
        return []

    def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
        if tmdb_id == 900005:
            return TmdbTitle(900005, "film", "Archipel Pilote", 2021, "Désert.", None, None, None, ())
        if tmdb_id == 99:
            air = datetime(2026, 1, 2, tzinfo=timezone.utc).date()
            return TmdbTitle(
                99,
                "series",
                "Demain",
                2026,
                None,
                None,
                "returning",
                air,
                (
                    TmdbSeason(
                        1,
                        "Saison 1",
                        None,
                        air,
                        1,
                        5,
                        (TmdbEpisode(1, 1, "Pilote", air, 50),),
                    ),
                ),
            )
        return TmdbTitle(tmdb_id, media_type, "Associé", 2024, None, None, "returning", None, ())

    def close(self) -> None:
        return None


def _touch(path: Path, payload: bytes = b"video") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _tree(root: Path) -> tuple[Path, Path]:
    films = root / "Films"
    series = root / "Series"
    _touch(films / "Archipel Pilote (2021)" / "Archipel Pilote (2021) [tmdbid-900005].mkv", b"archipel")
    _touch(series / "Exemple (2024)" / "Season 01" / "Exemple (2024) - S01E02.mkv", b"ex")
    _touch(series / "Ambigu (2020)" / "Season 01" / "Ambigu (2020) - S01E01.mkv", b"amb")
    _touch(series / "The.Show.S01E02.1080p.WEB-DL.x264-GROUP.mkv", b"rel")
    _touch(series / "Demain (2026)" / "Season 01" / "Demain (2026) - S01E01.mkv", b"dem")
    _touch(series / "Clip.sample.mkv", b"nope")
    return films, series


def test_scan_matches_tags_existing_titles_and_skips_ambiguous(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    films, series = _tree(Path(app.state.settings.data_dir) / "library")
    db = app.state.session_factory()
    try:
        report = scan_library(db, movies=films, series=series, tmdb=FakeTmdb())
        db.commit()
        assert report.followed == 2
        assert report.matched == 3
        assert report.review == 1
        assert report.unmatched == 1
        assert report.missing == 0
        archipel = db.query(Media).filter(Media.tmdb_id == 900005).one()
        film_item = db.query(MediaItem).filter(MediaItem.media_id == archipel.id).one()
        assert film_item.downloaded is True
        assert film_item.file_path.endswith("[tmdbid-900005].mkv")
        example = db.query(Media).filter(Media.title == "Exemple").one()
        episode = db.query(MediaItem).filter(MediaItem.media_id == example.id, MediaItem.episode == 2).one()
        assert episode.downloaded is True
        assert episode.state == "downloaded"
        ambiguous = db.query(LibraryFile).filter(LibraryFile.parsed_title == "Ambigu").one()
        assert ambiguous.status == "review"
        assert len(ambiguous.candidates) == 2
        assert db.query(Media).filter(Media.title == "Ambigu").count() == 0
        release = db.query(LibraryFile).filter(LibraryFile.parsed_title == "The Show").one()
        assert release.status == "unmatched"
        assert release.quality == "1080p"
        assert release.size == 3
        again = scan_library(db, movies=films, series=series, tmdb=FakeTmdb())
        db.commit()
        assert again.followed == 0
        assert db.query(Media).count() == 3
        assert db.query(LibraryFile).count() == 5
    finally:
        db.close()


def test_rescan_marks_a_removed_file_missing(client, app):
    login(client)
    films, series = _tree(Path(app.state.settings.data_dir) / "library-missing")
    db = app.state.session_factory()
    try:
        scan_library(db, movies=films, series=series, tmdb=FakeTmdb())
        db.commit()
        removed = next(path for path in films.rglob("*.mkv"))
        removed.unlink()
        report = scan_library(db, movies=films, series=series, tmdb=FakeTmdb())
        db.commit()
        assert report.missing == 1
        row = db.query(LibraryFile).filter(LibraryFile.path == str(removed.resolve())).one()
        assert row.status == "missing"
        item = db.get(MediaItem, row.media_item_id)
        assert item.downloaded is False
        assert item.file_path is None
        assert item.state == "missing"
    finally:
        db.close()


def test_manual_match_and_api_scan(client, app, monkeypatch):
    login(client)
    root = Path(app.state.settings.data_dir) / "library-api"
    films, series = _tree(root)
    monkeypatch.setattr("mediatracker.api.v1.library.TmdbClient", lambda token: FakeTmdb())
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "tmdb_read_token": "tmdb-test-token",
                "library_movies_path": str(films),
                "library_series_path": str(series),
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    scanned = client.post("/api/v1/library/scan", headers=csrf(client))
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["matched"] == 2
    pending = client.get("/api/v1/library/files")
    assert pending.status_code == 200
    titles = {item["parsed_title"] for item in pending.json()["items"]}
    assert titles == {"Ambigu", "The Show", "Exemple"}
    show = next(item for item in pending.json()["items"] if item["parsed_title"] == "The Show")
    matched = client.post(
        f"/api/v1/library/files/{show['id']}/match",
        json={"tmdb_id": 77, "type": "series"},
        headers=csrf(client),
    )
    assert matched.status_code == 200, matched.text
    assert matched.json()["status"] == "matched"
    assert matched.json()["media_id"]
    left = {item["parsed_title"] for item in client.get("/api/v1/library/files").json()["items"]}
    assert left == {"Ambigu", "Exemple"}
    episodes = client.get(f"/api/v1/media/{matched.json()['media_id']}/items").json()
    episode = episodes["seasons"][0]["episodes"][0]
    assert episode["downloaded"] is True
    assert episode["episode"] == 2


def test_pending_download_is_not_restarted_when_the_file_is_local(client, app):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Déjà là", "year": 2020},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"downloaded": True, "title": "Déjà là"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    refused = client.post(
        "/api/v1/links",
        json={"media_id": media_id, "url": "https://rapidgator.net/file/abc", "start": True},
        headers=csrf(client),
    )
    assert refused.status_code == 409

    folder = Path(app.state.settings.data_dir) / "media" / "Films" / "Déjà là (2020)"
    folder.mkdir(parents=True)
    video = folder / "Déjà là (2020).mkv"
    video.write_bytes(b"film")
    db = app.state.session_factory()
    try:
        stored_item = db.get(MediaItem, item.json()["id"])
        stored_item.file_path = str(video)
        row = Download(
            media_item_id=stored_item.id,
            state="pending",
            created_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.commit()
        download_id = row.id
    finally:
        db.close()

    class QuietDebrid:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def health(self) -> Health:
            return Health(status="ok", code=None, message="ok", checked_at=datetime.now(timezone.utc))

        def unlock(self, url: str) -> DirectLink:
            self.seen.append(url)
            return DirectLink(url="https://cdn.example/x.mkv", filename="x.mkv", size=1)

    db = app.state.session_factory()
    debrid = QuietDebrid()
    try:
        tick(db, app.state.settings, debrid=debrid, downloader=_UnusedDownloader())
        stored = db.get(Download, download_id)
        assert stored.state == "cancelled"
        assert stored.error_code == "ALREADY_LOCAL"
    finally:
        db.close()
    assert debrid.seen == []


def test_periodic_scan_respects_the_daily_interval(client, app):
    login(client)
    now = datetime(2026, 10, 11, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        first = maybe_scan_library(db, app.state.settings, now=now, tmdb=FakeTmdb())
        assert first is not None
        second = maybe_scan_library(db, app.state.settings, now=now + timedelta(hours=23), tmdb=FakeTmdb())
        assert second is None
        third = maybe_scan_library(db, app.state.settings, now=now + timedelta(hours=24), tmdb=FakeTmdb())
        assert third is not None
    finally:
        db.close()


def test_multi_episode_file_marks_every_episode_and_year_collision_stays_apart(client, app, tmp_path):
    login(client)
    for year in (1984, 2021):
        created = client.post(
            "/api/v1/media",
            json={"type": "film", "title": "Archipel Pilote", "year": year},
            headers=csrf(client),
        )
        assert created.status_code == 201, created.text
    series = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Exemple", "year": 2024},
        headers=csrf(client),
    )
    assert series.status_code == 201, series.text
    films = tmp_path / "Films"
    shows = tmp_path / "Series"
    _touch(films / "Archipel Pilote (1984)" / "Archipel Pilote (1984).mkv", b"old")
    _touch(films / "Archipel Pilote (2021)" / "Archipel Pilote (2021).mkv", b"new")
    _touch(films / "Archipel Pilote.mkv", b"which")
    _touch(shows / "Exemple (2024) - S01E01-E02.mkv", b"both")
    db = app.state.session_factory()
    try:
        scan_library(db, movies=films, series=shows, tmdb=FakeTmdb())
        db.commit()
        old = db.query(Media).filter(Media.title == "Archipel Pilote", Media.year == 1984).one()
        new = db.query(Media).filter(Media.title == "Archipel Pilote", Media.year == 2021).one()
        assert db.query(MediaItem).filter(MediaItem.media_id == old.id, MediaItem.downloaded.is_(True)).count() == 1
        assert db.query(MediaItem).filter(MediaItem.media_id == new.id, MediaItem.downloaded.is_(True)).count() == 1
        loose = db.query(LibraryFile).filter(LibraryFile.path.endswith("/Archipel Pilote.mkv")).one()
        assert loose.status == "review"
        assert loose.note and "Collision d'année" in loose.note
        show = db.query(Media).filter(Media.title == "Exemple").one()
        episodes = (
            db.query(MediaItem)
            .filter(MediaItem.media_id == show.id, MediaItem.downloaded.is_(True))
            .order_by(MediaItem.episode)
            .all()
        )
        assert [(row.season, row.episode) for row in episodes] == [(1, 1), (1, 2)]
        assert episodes[0].file_path == episodes[1].file_path
    finally:
        db.close()


def test_final_repack_and_proper_do_not_hide_the_episode():
    lanterns = parse_release("Lanternes Démo.S01E08.FiNAL.1080p.WEB.H264-GL0P-Wawacity.skin.mkv")
    assert (lanterns.title, lanterns.season, lanterns.episode) == ("Lanternes Démo", 1, 8)
    assert parse_release("Show.S01E08.PROPER.mkv").title == "Show"
    assert parse_release("Show.S01E02.REPACK.mkv").title == "Show"
    film = parse_release("Final Destination (2000).mkv")
    assert film.title == "Final Destination"
    assert film.season is None


def test_scan_attaches_a_final_release_name_to_the_fiche(client, app, tmp_path):
    del client
    series = tmp_path / "Series"
    folder = series / "Incoming"
    name = "Lanternes Démo.S01E08.FiNAL.1080p.WEB.H264-GL0P-Wawacity.skin.mkv"
    _touch(folder / name, b"episode")
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Lanternes Démo", year=2026, tmdb_id=900104, followed=False)
        db.add(show)
        db.commit()
        report = scan_library(
            db,
            movies=tmp_path / "Films",
            series=series,
            now=datetime(2026, 10, 15, tzinfo=timezone.utc),
        )
        db.commit()
        assert report.matched == 1
        stored = db.query(LibraryFile).one()
        assert stored.media_id == show.id
        assert (stored.season, stored.episode) == (1, 8)
        assert stored.parsed_title == "Lanternes Démo"
        item = db.query(MediaItem).filter(MediaItem.media_id == show.id).one()
        assert (item.season, item.episode) == (1, 8)
        assert item.downloaded is True
    finally:
        db.close()


class _UnusedDownloader:
    def add(self, url: str, dest_dir: Path, filename: str | None, headers=None) -> str:
        raise AssertionError(url)

    def status(self, handle: str) -> DownloadStatus:
        raise AssertionError(handle)

    def remove(self, handle: str, delete_files: bool = False) -> None:
        return None

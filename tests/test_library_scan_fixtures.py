"""Realistic library scan: tags, release folders, sidecars, films, and duplicates.

The scan attaches videos to fiches and marks them downloaded. It does not create
folders, rename files, or trash a second copy of the same episode.
"""

from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.core.catalog import fold_title
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbHit, TmdbTitle
from mediatracker.library.parse import parse_path
from mediatracker.library.scanner import scan_library

DEMO_BUILDING_TMDB = 900001
DEMO_ARCHIPEL_TMDB = 900005
DEMO_LIMITE_TMDB = 900006
DEMO_BRUME_TMDB = 900007
DETECTIVE_DEMO_TMDB = 2511
NIGHT = 900109


def test_tagged_season_folder_keeps_its_tmdb_id_and_a_foreign_file_does_not(tmp_path):
    series = tmp_path / "Series"
    tagged = series / "Immeuble Démo (2021) [tmdbid-900001]" / "Season 05"
    own = parse_path(
        tagged / "Immeuble Démo (2021) - S05E05.mkv",
        root=series,
        root_kind="series",
    )
    assert (own.title, own.year, own.tmdb_id, own.season, own.episode) == (
        "Immeuble Démo",
        2021,
        DEMO_BUILDING_TMDB,
        5,
        5,
    )
    stray = series / "Immeuble Démo FRENCH TFA Wawacity town" / "Season 05"
    release = parse_path(
        stray / "Immeuble Démo FRENCH TFA Wawacity town - S05E07.mkv",
        root=series,
        root_kind="series",
    )
    assert release.title == "Immeuble Démo"
    assert release.year is None
    assert (release.season, release.episode, release.tmdb_id) == (5, 7, None)
    foreign = parse_path(
        tagged.parent / "Season 01" / "Détective Démo (2025) - S01E01.mkv",
        root=series,
        root_kind="series",
    )
    assert (foreign.title, foreign.year, foreign.tmdb_id, foreign.episode) == ("Détective Démo", 2025, None, 1)


class _Catalog:
    def __init__(self, token: str | None = None, **_kwargs) -> None:
        del token, _kwargs

    def search(self, query: str, media_type: str) -> list[TmdbHit]:
        key = (fold_title(query), media_type)
        return list(_SEARCH.get(key, []))

    def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
        title, year = _FETCH[tmdb_id]
        return TmdbTitle(tmdb_id, media_type, title, year, None, None, None, None, ())

    def close(self) -> None:
        return None


def _flat(value: str) -> str:
    return " ".join((value or "").lower().split())


_SEARCH = {
    ("immeuble demo", "series"): [
        TmdbHit(DEMO_BUILDING_TMDB, "series", "Immeuble Démo", 2021, None, None)
    ],
    ("limite demo", "film"): [TmdbHit(DEMO_LIMITE_TMDB, "film", "Limite Démo", 2010, None, None)],
    ("brume demo", "film"): [TmdbHit(DEMO_BRUME_TMDB, "film", "Brume Démo", 1995, None, None)],
    ("detective demo", "series"): [TmdbHit(DETECTIVE_DEMO_TMDB, "series", "Détective Démo", 2025, None, None)],
    ("nuit demo", "series"): [TmdbHit(NIGHT, "series", "Nuit Démo", 2016, None, None)],
    ("bureau demo", "series"): [
        TmdbHit(1, "series", "Bureau Démo", 2005, None, None),
        TmdbHit(2, "series", "Bureau Démo", 2001, None, None),
    ],
    ("ambigu", "series"): [
        TmdbHit(11, "series", "Ambigu", 2020, None, None),
        TmdbHit(12, "series", "Ambigu", 2020, None, None),
    ],
    ("archipel pilote", "film"): [TmdbHit(DEMO_ARCHIPEL_TMDB, "film", "Archipel Pilote", 2021, None, None)],
}

_FETCH = {
    DEMO_BUILDING_TMDB: ("Immeuble Démo", 2021),
    DEMO_ARCHIPEL_TMDB: ("Archipel Pilote", 2021),
    DEMO_LIMITE_TMDB: ("Limite Démo", 2010),
    DEMO_BRUME_TMDB: ("Brume Démo", 1995),
    DETECTIVE_DEMO_TMDB: ("Détective Démo", 2025),
    NIGHT: ("Nuit Démo", 2016),
}


def _touch(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _dirs(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()}


def _library(root: Path) -> tuple[Path, Path, Path, Path]:
    films = root / "Films"
    series = root / "Series"
    _touch(films / "Archipel Pilote (2021) [tmdbid-900005]" / "Archipel Pilote (2021).mkv", b"archipel-film")
    _touch(films / "Limite Démo (2010)" / "Limite Démo (2010).mkv", b"small")
    _touch(films / "Limite Démo (2010)" / "Limite Démo (2010).1080p.mkv", b"limit-large")
    _touch(films / "Limite Démo (2010)" / "Limite Démo.sample.mkv", b"skip-me")
    _touch(films / "Brume Démo (1995)" / "Brume Démo (1995).mkv", b"brume")
    tagged = series / "Immeuble Démo (2021) [tmdbid-900001]"
    _touch(tagged / "Season 05" / "Immeuble Démo (2021) - S05E05.mkv", b"building-large")
    _touch(tagged / "Season 05" / "Immeuble Démo (2021) - S05E05.nfo", b"<nfo>")
    _touch(tagged / "Season 05" / "Immeuble Démo (2021) - S05E05-thumb.jpg", b"jpg")
    _touch(tagged / "Season 01" / "Détective Démo (2025) - S01E01.mkv", b"detective-misplaced")
    plain = series / "Immeuble Démo (2021)"
    _touch(plain / "Season 02" / "Immeuble Démo (2021) - S02E01.mkv", b"s02")
    stray = series / "Immeuble Démo FRENCH TFA Wawacity town"
    _touch(stray / "Season 05" / "Immeuble Démo FRENCH TFA Wawacity town - S05E07.mkv", b"s07")
    _touch(stray / "Season 05" / "Immeuble Démo FRENCH TFA Wawacity town - S05E07.nfo", b"<nfo>")
    _touch(stray / "Season 05" / "Immeuble Démo FRENCH TFA Wawacity town - S05E07-thumb.jpg", b"jpg")
    _touch(stray / "Season 05" / "Immeuble Démo FRENCH TFA Wawacity town - S05E05.mkv", b"tiny")
    detective = series / "Détective Démo (2025) [tmdbid-2511]" / "Season 01"
    _touch(detective / "Détective Démo (2025) - S01E04.mkv", b"detective")
    night = series / "Nuit Démo FRENCH TFA Wawacity town" / "Season 01"
    _touch(night / "Nuit Démo FRENCH TFA Wawacity town - S01E01.mkv", b"night")
    _touch(series / "Bureau Démo" / "Season 01" / "Bureau Démo - S01E01.mkv", b"office")
    _touch(series / "Ambigu (2020)" / "Season 01" / "Ambigu (2020) - S01E01.mkv", b"amb")
    _touch(series / "Ghost FRENCH TFA Wawacity town" / "Season 01" / "Ghost - S01E01.nfo", b"only-sidecar")
    return films, series, tagged, stray


def test_scan_matches_realistic_library_without_creating_folders(client, app, monkeypatch, tmp_path):
    films, series, _tagged, stray = _library(tmp_path)
    before = _dirs(tmp_path)
    db = app.state.session_factory()
    try:
        demo_building = Media(
            type="series",
            title="Immeuble Démo",
            year=2021,
            tmdb_id=DEMO_BUILDING_TMDB,
            followed=True,
        )
        limit_demo = Media(type="film", title="Limite Démo", year=2010, followed=True)
        brume_demo = Media(type="film", title="Brume Démo", year=1995, followed=False)
        db.add_all([demo_building, limit_demo, brume_demo])
        db.flush()
        episode = MediaItem(media_id=demo_building.id, season=2, episode=1, state="available", downloaded=False)
        db.add(episode)
        db.flush()
        pending = Download(media_item_id=episode.id, state="pending", created_at=datetime.now(timezone.utc))
        db.add(pending)
        db.commit()
        demo_building_id, limit_id, brume_id, episode_id, pending_id = (
            demo_building.id,
            limit_demo.id,
            brume_demo.id,
            episode.id,
            pending.id,
        )

        report = scan_library(db, movies=films, series=series, tmdb=_Catalog())
        db.commit()

        assert report.seen == 13
        assert report.matched == 11
        assert report.review == 2
        assert report.unmatched == 0
        assert report.missing == 0
        assert report.followed == 3
        assert _dirs(tmp_path) == before
        assert stray.joinpath("Season 05").joinpath(
            "Immeuble Démo FRENCH TFA Wawacity town - S05E07.nfo"
        ).is_file()

        titles = [row.title for row in db.query(Media).order_by(Media.title).all()]
        assert titles.count("Immeuble Démo") == 1
        assert titles.count("Limite Démo") == 1
        assert titles.count("Brume Démo") == 1
        assert titles.count("Archipel Pilote") == 1
        assert titles.count("Détective Démo") == 1
        assert titles.count("Nuit Démo") == 1
        assert "Bureau Démo" not in titles
        assert "Ambigu" not in titles
        assert all("wawacity" not in title.casefold() for title in titles)
        assert db.query(Media).count() == 6

        show = db.get(Media, demo_building_id)
        slots = {
            (row.season, row.episode): row
            for row in db.query(MediaItem).filter(MediaItem.media_id == show.id).all()
        }
        assert set(slots) == {(5, 5), (2, 1), (5, 7)}
        for item in slots.values():
            assert item.downloaded is True
            assert item.state == "downloaded"
            assert item.file_path
        kept = slots[(5, 5)]
        assert kept.file_path.endswith("Immeuble Démo (2021) - S05E05.mkv")
        assert Path(kept.file_path).read_bytes() == b"building-large"
        copies = db.query(LibraryFile).filter(LibraryFile.media_item_id == kept.id).all()
        assert len(copies) == 2
        assert {row.tmdb_id for row in copies} == {DEMO_BUILDING_TMDB}
        assert db.get(Download, pending_id).state == "cancelled"
        assert db.get(Download, pending_id).error_code == "ALREADY_LOCAL"
        assert db.get(MediaItem, episode_id).downloaded is True

        film = db.get(Media, limit_id)
        assert film.tmdb_id == DEMO_LIMITE_TMDB
        film_items = db.query(MediaItem).filter(MediaItem.media_id == film.id).all()
        assert len(film_items) == 1
        assert film_items[0].season is None
        assert film_items[0].downloaded is True
        assert Path(film_items[0].file_path).read_bytes() == b"limit-large"
        assert db.query(LibraryFile).filter(LibraryFile.media_id == film.id).count() == 2

        brume_row = db.get(Media, brume_id)
        assert brume_row.followed is False
        assert brume_row.tmdb_id == DEMO_BRUME_TMDB
        assert db.query(Media).filter(Media.title == "Brume Démo").count() == 1
        brume_item = db.query(MediaItem).filter(MediaItem.media_id == brume_row.id).one()
        assert brume_item.downloaded is True

        archipel = db.query(Media).filter(Media.tmdb_id == DEMO_ARCHIPEL_TMDB).one()
        assert archipel.type == "film"
        archipel_item = db.query(MediaItem).filter(MediaItem.media_id == archipel.id).one()
        assert archipel_item.downloaded is True
        assert archipel_item.episode is None

        detective_demo = db.query(Media).filter(Media.tmdb_id == DETECTIVE_DEMO_TMDB).one()
        assert detective_demo.type == "series"
        detective_slots = {
            (row.season, row.episode)
            for row in db.query(MediaItem).filter(
                MediaItem.media_id == detective_demo.id,
                MediaItem.downloaded.is_(True),
            )
        }
        assert detective_slots == {(1, 1), (1, 4)}
        detective_files = db.query(LibraryFile).filter(LibraryFile.parsed_title == "Détective Démo").all()
        assert detective_files
        assert all(row.media_id == detective_demo.id for row in detective_files)

        night = db.query(Media).filter(Media.tmdb_id == NIGHT).one()
        assert night.title == "Nuit Démo"
        assert night.year == 2016
        night_item = db.query(MediaItem).filter(MediaItem.media_id == night.id, MediaItem.downloaded.is_(True)).one()
        assert (night_item.season, night_item.episode) == (1, 1)
        night_file = db.query(LibraryFile).filter(LibraryFile.media_id == night.id).one()
        assert night_file.parsed_title == "Nuit Démo"
        assert "Wawacity" not in night_file.parsed_title

        office = db.query(LibraryFile).filter(LibraryFile.parsed_title == "Bureau Démo").one()
        assert office.status == "review"
        assert office.media_id is None
        ambigu = db.query(LibraryFile).filter(LibraryFile.parsed_title == "Ambigu").one()
        assert ambigu.status == "review"
        assert ambigu.media_id is None
        assert db.query(LibraryFile).filter(LibraryFile.path.endswith(".nfo")).count() == 0
        assert db.query(LibraryFile).filter(LibraryFile.path.like("%sample%")).count() == 0

        again = scan_library(db, movies=films, series=series, tmdb=_Catalog())
        db.commit()
        assert again.followed == 0
        assert again.matched == 11
        assert again.missing == 0
        assert db.query(Media).count() == 6
        assert db.query(LibraryFile).count() == 13
        assert _dirs(tmp_path) == before
        assert db.get(MediaItem, kept.id).downloaded is True
        assert db.get(MediaItem, kept.id).file_path == kept.file_path
    finally:
        db.close()

    login(client)
    monkeypatch.setattr(
        "mediatracker.api.v1.library.borrow_client",
        lambda _db, _settings: (_Catalog(), True),
    )
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
    body = scanned.json()
    assert body["followed"] == 0
    assert body["matched"] == 11
    assert body["review"] == 2
    assert _dirs(tmp_path) == before

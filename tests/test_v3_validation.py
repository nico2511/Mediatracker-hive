"""V3 — validation only: Jellyfin layout, dry-run before moves, collisions without numeric suffix."""

from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login
from tests.test_pipeline import FakeDebrid, FakeDownloader

from mediatracker.core.naming import filing_folder, library_destination
from mediatracker.core.pipeline import tick
from mediatracker.db.models import LibraryFile, Media, MediaItem, TrashEntry
from mediatracker.library.jellyfin_align import apply_renames, build_report
from mediatracker.library.regroup import apply_regroup, plan_regroup

NOW = datetime(2026, 10, 23, 9, tzinfo=timezone.utc)

FILM_TITLE = "Lanterne Volée"
FILM_YEAR = 2023
FILM_TMDB = 88001
SERIES_TITLE = "Brume Céleste"
SERIES_YEAR = 2021
SERIES_TMDB = 88002


def _tick(db, app, downloader):
    tick(db, app.state.settings, now=NOW, debrid=FakeDebrid(), downloader=downloader)


def test_v3_library_destination_matches_jellyfin_layout(tmp_path):
    movies = tmp_path / "Films"
    series = tmp_path / "Series"
    film = library_destination(
        movies_dir=movies,
        series_dir=series,
        media_type="film",
        title=FILM_TITLE,
        year=FILM_YEAR,
        season=None,
        episode=None,
        ext=".mkv",
        tmdb_id=FILM_TMDB,
    )
    assert film.parent.name == f"{FILM_TITLE} ({FILM_YEAR}) [tmdbid-{FILM_TMDB}]"
    assert film.name == f"{FILM_TITLE} ({FILM_YEAR}).mkv"

    episode = library_destination(
        movies_dir=movies,
        series_dir=series,
        media_type="series",
        title=SERIES_TITLE,
        year=SERIES_YEAR,
        season=1,
        episode=2,
        ext=".mkv",
        tmdb_id=SERIES_TMDB,
        library_folder=filing_folder(series, title=SERIES_TITLE, year=SERIES_YEAR, tmdb_id=SERIES_TMDB),
    )
    assert episode.parent.parent.name == f"{SERIES_TITLE} ({SERIES_YEAR}) [tmdbid-{SERIES_TMDB}]"
    assert episode.parent.name == "Season 01"
    assert episode.name == f"{SERIES_TITLE} ({SERIES_YEAR}) - S01E02.mkv"


def test_v3_regroup_plan_is_a_dry_run(tmp_path, client, app):
    login(client)
    films = tmp_path / "Films"
    series = tmp_path / "Series"
    release = series / "Brume Céleste FRENCH SAMPLE hoster town"
    (release / "Season 01").mkdir(parents=True)
    episode = release / "Season 01" / "Brume.Céleste.S01E03.mkv"
    episode.write_bytes(b"v")
    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title=SERIES_TITLE,
            year=SERIES_YEAR,
            tmdb_id=SERIES_TMDB,
            followed=True,
        )
        db.add(media)
        db.commit()
        report = plan_regroup(db, films, series)
        group = next(row for row in report.groups if row.media_id == media.id)
        assert group.moves
        assert episode.is_file()
        applied, rename_id, _after = apply_regroup(db, films, series, [media.id], validated=False)
        assert applied is False
        assert rename_id is None
        assert episode.is_file()
    finally:
        db.close()


def test_v3_regroup_collision_trashes_loser_without_numeric_suffix(tmp_path, client, app):
    login(client)
    films = tmp_path / "Films"
    series = tmp_path / "Series"
    junk = series / "Brume Céleste FRENCH SAMPLE hoster surf"
    (junk / "Season 01").mkdir(parents=True)
    incoming = junk / "Season 01" / "Brume.Céleste.S01E01.mkv"
    incoming.write_bytes(b"new")
    canonical = series / f"{SERIES_TITLE} ({SERIES_YEAR}) [tmdbid-{SERIES_TMDB}]" / "Season 01"
    canonical.mkdir(parents=True)
    kept_name = f"{SERIES_TITLE} ({SERIES_YEAR}) - S01E01.mkv"
    (canonical / kept_name).write_bytes(b"old")
    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title=SERIES_TITLE,
            year=SERIES_YEAR,
            tmdb_id=SERIES_TMDB,
            followed=True,
        )
        db.add(media)
        db.commit()
        report = plan_regroup(db, films, series)
        group = next(row for row in report.groups if row.media_id == media.id)
        trashed = next(move for move in group.moves if move.action == "trash")
        assert trashed.name == incoming.name
        assert ".1." not in trashed.dest
        assert not any(".1.mkv" in move.dest for move in group.moves)
        applied, rename_id, _after = apply_regroup(db, films, series, [media.id], validated=True)
        db.commit()
        assert applied is True
        assert rename_id is None  # duplicate episode: trash only, no folder rename journal
        assert (canonical / kept_name).read_bytes() == b"old"
        assert not incoming.exists()
        trash_copy = list((series.parent / ".trash").rglob(incoming.name))
        assert len(trash_copy) == 1
        assert trash_copy[0].read_bytes() == b"new"
        assert not any(".1.mkv" in path.name for path in series.rglob("*.mkv"))
    finally:
        db.close()


def test_v3_regroup_api_requires_validation(client, app):
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    release = series / "Brume Céleste FRENCH SAMPLE hoster farm"
    (release / "Season 01").mkdir(parents=True)
    episode = release / "Season 01" / "Brume.Céleste.S01E05.mkv"
    episode.write_bytes(b"x")
    db = app.state.session_factory()
    try:
        db.add(
            Media(
                type="series",
                title=SERIES_TITLE,
                year=SERIES_YEAR,
                tmdb_id=SERIES_TMDB,
                followed=True,
            )
        )
        db.commit()
    finally:
        db.close()
    login(client)
    preview = client.post(
        "/api/v1/library/regroup",
        json={"media_ids": [], "validated": False},
        headers=csrf(client),
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["applied"] is False
    assert episode.is_file()
    listed = client.get("/api/v1/library/regroup")
    assert listed.status_code == 200
    group = next(row for row in listed.json()["groups"] if row["title"] == SERIES_TITLE)
    applied = client.post(
        "/api/v1/library/regroup",
        json={"media_ids": [group["media_id"]], "validated": True},
        headers=csrf(client),
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    dest = (
        series
        / f"{SERIES_TITLE} ({SERIES_YEAR}) [tmdbid-{SERIES_TMDB}]"
        / "Season 01"
        / f"{SERIES_TITLE} ({SERIES_YEAR}) - S01E05.mkv"
    )
    assert dest.is_file()
    assert not episode.exists()


def _harbor_episode(client) -> tuple[int, int]:
    created = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Harbor Lantern", "year": 2022},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    episode = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 7, "title": "Épisode 7"},
        headers=csrf(client),
    )
    assert episode.status_code == 201, episode.text
    return media_id, episode.json()["id"]


def test_v3_filing_never_writes_numeric_duplicate_suffix(client, app):
    """When a better download replaces a library copy, the loser goes to trash — never ``.1.mkv``."""
    from mediatracker.db.models import Download, Link, Provider

    login(client)
    media_id, item_id = _harbor_episode(client)
    series = Path(app.state.settings.data_dir) / "media" / "Series"
    season = series / "Harbor Lantern (2022) [tmdbid-99001]" / "Season 01"
    season.mkdir(parents=True)
    dest = season / "Harbor Lantern (2022) - S01E07.mkv"
    dest.write_bytes(b"o" * 1000)
    source = Path(app.state.settings.data_dir) / "downloads" / "incomplete" / "Harbor Lantern - S01E07.mkv"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"n" * 4000)
    db = app.state.session_factory()
    try:
        media = db.get(Media, media_id)
        media.tmdb_id = 99001
        db.add(
            LibraryFile(
                path=str(dest.resolve()),
                size=1000,
                parsed_title="Harbor Lantern",
                parsed_year=2022,
                season=1,
                episode=7,
                media_type="series",
                media_id=media_id,
                media_item_id=item_id,
                status="matched",
                release_quality="720p",
                seen_at=NOW,
            )
        )
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        link = Link(
            media_item_id=item_id,
            provider_id=provider.id,
            kind="hoster",
            url="https://example.test/harbor-better",
            quality="1080p",
            filename="Harbor.Lantern.S01E07.1080p.WEB.mkv",
            external_id="harbor-better",
            discovered_at=NOW,
        )
        db.add(link)
        db.flush()
        row = Download(
            media_item_id=item_id,
            link_id=link.id,
            state="post_processing",
            filename=source.name,
            error=str(source),
            created_at=NOW,
        )
        db.add(row)
        db.commit()
        _tick(db, app, FakeDownloader())
        db.refresh(row)
        item = db.get(MediaItem, item_id)
        assert row.state == "done", row.error_code
        assert dest.read_bytes() == b"n" * 4000
        assert not source.exists()
        assert not any(".1." in path.name for path in season.iterdir())
        assert db.query(TrashEntry).count() == 1
        assert item.file_path == str(dest)
    finally:
        db.close()


def test_v3_jellyfin_rename_preview_does_not_move(client, app):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.test",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": "11111111-1111-1111-1111-111111111111",
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": FILM_TITLE, "year": FILM_YEAR, "tmdb_id": FILM_TMDB},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"title": FILM_TITLE},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    root = Path(app.state.settings.data_dir) / "media" / "Films"
    src = root / "Dossier provisoire" / f"{FILM_TITLE}.mkv"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"clip")
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.json()["id"])
        row.file_path = str(src)
        row.downloaded = True
        media = db.get(Media, media_id)
        media.jellyfin_series_id = "film-jf-1"
        db.commit()
        from mediatracker.db.models import JellyfinLibraryItem

        db.add(
            JellyfinLibraryItem(
                jellyfin_id="film-jf-1",
                item_type="Movie",
                name=FILM_TITLE,
                year=FILM_YEAR,
                tmdb_id=FILM_TMDB,
                synced_at=NOW,
            )
        )
        db.commit()
        report = build_report(db, app.state.settings)
        assert report["renames"]
        preview = client.post(
            "/api/v1/library/jellyfin/rename",
            json={"validated": False},
            headers=csrf(client),
        )
        assert preview.status_code == 200, preview.text
        assert preview.json()["applied"] is False
        assert src.is_file()
        result = apply_renames(db, app.state.settings, None, validated=False, now=NOW)
        assert result["applied"] is False
        assert result["moved"] == 0
        assert src.is_file()
    finally:
        db.close()


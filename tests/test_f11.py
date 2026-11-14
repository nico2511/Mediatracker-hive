"""F11: canonical probes on fiche open and targeted reanalyze."""

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.forget import TRACE_FILE_ABSENT
from mediatracker.core.naming import canonical_folder_name
from mediatracker.db.models import LibraryFile, Media, MediaItem

NOW = datetime(2026, 10, 22, 15, tzinfo=timezone.utc)


def _series(db, title: str = "Brume Roche", tmdb_id: int = 4401) -> Media:
    media = Media(type="series", title=title, year=2025, tmdb_id=tmdb_id, followed=True)
    db.add(media)
    db.flush()
    return media


def _episode(db, media: Media, season: int, episode: int, **kwargs) -> MediaItem:
    item = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"Épisode {episode}",
        air_date=date(2025, 3, episode),
        state="missing",
        downloaded=False,
        **kwargs,
    )
    db.add(item)
    db.flush()
    return item


def _layout(app):
    data = Path(app.state.settings.data_dir)
    movies = data / "media" / "Films"
    series = data / "media" / "Series"
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    return movies, series


def test_open_fiche_does_not_rglob_on_local_disk(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db)
        _episode(
            db,
            media,
            1,
            2,
            traces=[{"kind": TRACE_FILE_ABSENT, "at": NOW.isoformat()}],
        )
        db.commit()
        release = series / "Brume.Roche.S01E02.FINAL.mkv"
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_bytes(b"clip")
    finally:
        db.close()

    with patch.object(Path, "rglob", side_effect=AssertionError("rglob interdit à l'ouverture")):
        response = client.get(f"/api/v1/media/{media.id}")
    assert response.status_code == 200
    assert response.json()["file_status"] == "absent"


def test_absent_file_reappears_in_canonical_folder(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Cristal Nord")
        item = _episode(
            db,
            media,
            1,
            1,
            traces=[{"kind": TRACE_FILE_ABSENT, "at": NOW.isoformat()}],
        )
        folder = series / canonical_folder_name("Cristal Nord", 2025, 4401) / "Season 01"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "Cristal Nord (2025) [tmdbid-4401] - S01E01.mkv"
        video.write_bytes(b"clip")
        db.commit()
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media.id}")
    assert response.status_code == 200
    body = response.json()
    assert body["file_status"] == "present"
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.id)
        assert row.downloaded is True
        assert Path(row.file_path) == video.resolve()
        library = db.scalar(select(LibraryFile).where(LibraryFile.media_item_id == item.id))
        assert library is not None
        assert library.status == "matched"
        assert library.missing_at is None
    finally:
        db.close()


def test_reanalyze_fiche_and_season(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Delta Lac")
        _episode(db, media, 1, 1)
        _episode(db, media, 1, 2)
        _episode(db, media, 2, 1)
        folder_s1 = series / canonical_folder_name("Delta Lac", 2025, 4401) / "Season 01"
        folder_s1.mkdir(parents=True, exist_ok=True)
        (folder_s1 / "Delta Lac (2025) [tmdbid-4401] - S01E01.mkv").write_bytes(b"a")
        (folder_s1 / "Delta Lac (2025) [tmdbid-4401] - S01E02.mkv").write_bytes(b"b")
        folder_s2 = series / canonical_folder_name("Delta Lac", 2025, 4401) / "Season 02"
        folder_s2.mkdir(parents=True, exist_ok=True)
        (folder_s2 / "Delta Lac (2025) [tmdbid-4401] - S02E01.mkv").write_bytes(b"c")
        db.commit()
    finally:
        db.close()

    season = client.post(f"/api/v1/media/{media.id}/seasons/1/reanalyze", headers=csrf(client))
    assert season.status_code == 200, season.text
    season_body = season.json()
    assert season_body["found"] == 2
    assert season_body["new"] >= 2

    whole = client.post(f"/api/v1/media/{media.id}/reanalyze", headers=csrf(client))
    assert whole.status_code == 200, whole.text
    whole_body = whole.json()
    assert whole_body["found"] == 3


def test_reanalyze_does_not_move_files(client, app, monkeypatch):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Echo Val")
        _episode(db, media, 1, 1)
        folder = series / canonical_folder_name("Echo Val", 2025, 4401) / "Season 01"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "Echo Val (2025) [tmdbid-4401] - S01E01.mkv"
        video.write_bytes(b"x")
        db.commit()
    finally:
        db.close()

    def forbid_move(*_args, **_kwargs):
        raise AssertionError("déplacement interdit")

    monkeypatch.setattr("mediatracker.core.naming.atomic_move", forbid_move)
    monkeypatch.setattr("os.replace", forbid_move)
    response = client.post(f"/api/v1/media/{media.id}/reanalyze", headers=csrf(client))
    assert response.status_code == 200, response.text
    assert video.exists()


def test_non_local_disk_falls_back_to_full_search(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        media = _series(db, title="Fable Mer")
        item = _episode(
            db,
            media,
            1,
            3,
            traces=[{"kind": TRACE_FILE_ABSENT, "at": NOW.isoformat()}],
        )
        release = series / "Import Fable" / "Fable.Mer.S01E03.REPACK.mkv"
        release.parent.mkdir(parents=True, exist_ok=True)
        release.write_bytes(b"clip")
        db.commit()
    finally:
        db.close()

    with patch("mediatracker.core.storage.library_on_local_disk", return_value=False):
        response = client.get(f"/api/v1/media/{media.id}")
    assert response.status_code == 200
    assert response.json()["file_status"] == "present"
    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.id)
        assert row.downloaded is True
        assert Path(row.file_path) == release.resolve()
    finally:
        db.close()

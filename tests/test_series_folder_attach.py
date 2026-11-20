"""Light attach from the series folder when episode slots lack a file path."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.db.models import Media, MediaItem
from mediatracker.library.series_folder_attach import (
    SERIES_FOLDER_ATTACH_COOLDOWN,
    attach_series_folder_if_needed,
    reset_series_folder_attach_for_tests,
)

NOW = datetime(2026, 10, 22, 14, tzinfo=timezone.utc)
TMDB_DEMO = 88042


def _layout(app):
    data = app.state.settings.data_dir
    movies = data / "media" / "Films"
    series = data / "media" / "Series"
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    return movies, series


def _series_on_disk(
    series_root,
    title: str = "Œuvre test",
    *,
    tagged: bool = True,
    year: int = 2026,
    payload: bytes = b"demo-ep",
) -> Path:
    name = f"{title} ({year})"
    if tagged:
        name = f"{name} [tmdbid-{TMDB_DEMO}]"
    folder = series_root / name
    path = folder / f"{title} - S01E01.mkv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _seed_series(db, *, title: str = "Œuvre test", followed: bool = True) -> tuple[Media, MediaItem]:
    media = Media(type="series", title=title, year=2026, tmdb_id=TMDB_DEMO, followed=followed)
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=1,
        episode=1,
        title="Pilotage",
        state="missing",
        downloaded=False,
    )
    db.add(item)
    db.flush()
    return media, item


def test_get_media_attaches_episode_from_series_folder(client, app):
    login(client)
    reset_series_folder_attach_for_tests()
    _movies, series = _layout(app)
    _series_on_disk(series)
    db = app.state.session_factory()
    try:
        media, item = _seed_series(db)
        db.commit()
        media_id = media.id
        assert not item.file_path
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}")
    assert response.status_code == 200, response.text

    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item.id)
        assert row is not None
        assert row.file_path
        assert row.downloaded is True
    finally:
        db.close()


def test_cooldown_blocks_second_pass(client, app):
    login(client)
    reset_series_folder_attach_for_tests()
    _movies, series = _layout(app)
    _series_on_disk(series)
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media, item = _seed_series(db)
        db.add(
            MediaItem(
                media_id=media.id,
                season=1,
                episode=2,
                title="Suite",
                state="missing",
                downloaded=False,
            )
        )
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        first = attach_series_folder_if_needed(db, settings, media, items, NOW)
        second = attach_series_folder_if_needed(
            db,
            settings,
            media,
            items,
            NOW + SERIES_FOLDER_ATTACH_COOLDOWN - timedelta(minutes=1),
        )
        assert first == 1
        assert second == 0
    finally:
        db.close()


def test_cooldown_applies_when_nothing_attached(client, app):
    login(client)
    reset_series_folder_attach_for_tests()
    _movies, series = _layout(app)
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media, item = _seed_series(db)
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        first = attach_series_folder_if_needed(db, settings, media, items, NOW)
        _series_on_disk(series)
        second = attach_series_folder_if_needed(
            db,
            settings,
            media,
            items,
            NOW + timedelta(minutes=1),
        )
        later = attach_series_folder_if_needed(
            db,
            settings,
            media,
            items,
            NOW + SERIES_FOLDER_ATTACH_COOLDOWN,
        )
        assert first == 0
        assert second == 0
        assert later == 1
        assert item.file_path
    finally:
        db.close()


def test_ambiguous_untagged_folders_are_not_attached(client, app):
    login(client)
    reset_series_folder_attach_for_tests()
    _movies, series = _layout(app)
    first_path = _series_on_disk(series, tagged=False, year=2025, payload=b"other-year")
    second_path = _series_on_disk(series, tagged=False, year=2026, payload=b"same-year")
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media, item = _seed_series(db)
        items = [item]
        attached = attach_series_folder_if_needed(db, settings, media, items, NOW)
        assert attached == 0
        assert not item.file_path
        assert first_path.is_file() and second_path.is_file()
    finally:
        db.close()


def test_tmdb_tag_wins_over_similar_untagged_folder(client, app):
    login(client)
    reset_series_folder_attach_for_tests()
    _movies, series = _layout(app)
    _series_on_disk(series, tagged=False, year=2025, payload=b"other-year")
    tagged = _series_on_disk(series, tagged=True, year=2026, payload=b"tagged-ep")
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media, item = _seed_series(db)
        attached = attach_series_folder_if_needed(db, settings, media, [item], NOW)
        assert attached == 1
        assert item.file_path == str(tagged.resolve())
    finally:
        db.close()


def test_follow_existing_attaches_series_folder(client, app):
    login(client)
    reset_series_folder_attach_for_tests()
    _movies, series = _layout(app)
    _series_on_disk(series, title="Suite test")
    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title="Suite test",
            year=2026,
            tmdb_id=TMDB_DEMO,
            followed=False,
        )
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            title="Entrée",
            state="missing",
            downloaded=False,
        )
        db.add(item)
        db.commit()
        media_id = media.id
        item_id = item.id
    finally:
        db.close()

    followed = client.post(f"/api/v1/media/{media_id}/follow", json={}, headers=csrf(client))
    assert followed.status_code == 200, followed.text

    db = app.state.session_factory()
    try:
        row = db.get(MediaItem, item_id)
        assert row is not None and row.file_path
    finally:
        db.close()

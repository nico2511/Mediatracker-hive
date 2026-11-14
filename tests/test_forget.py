"""Shared forget_local_file: voluntary trash vs unexpected absence."""

from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from mediatracker.core.cleanup import PlannedFile, _move_all
from mediatracker.core.forget import (
    TRACE_DELETED_VOLUNTARILY,
    TRACE_FILE_ABSENT,
    forget_local_file,
    is_file_absent,
    is_voluntarily_deleted,
)
from mediatracker.core.presence import local_video_in
from mediatracker.core.source_poll import expected_slots, refresh_library_claims
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import Media, MediaItem

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
TODAY = NOW.date()


def _series(db, title: str = "Brume Port") -> Media:
    media = Media(type="series", title=title, year=2024, tmdb_id=9001, followed=True, new_episode_policy="auto")
    db.add(media)
    db.flush()
    return media


def _episode(db, media: Media, **overrides) -> MediaItem:
    values = dict(
        media_id=media.id,
        season=1,
        episode=2,
        title="Quai Nord",
        air_date=date(2025, 6, 1),
        state="downloaded",
        downloaded=True,
        watched=False,
    )
    values.update(overrides)
    item = MediaItem(**values)
    db.add(item)
    db.flush()
    return item


def test_film_obtained_poll_cleared_on_unexpected_absence(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Zénith", year=2024, tmdb_id=9010, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            title="Zénith",
            state="downloaded",
            downloaded=True,
            file_path="/fiction/Zenith.mkv",
        )
        db.add(item)
        media.last_poll_result = "obtained"
        media.last_poll_message = "Fichier obtenu."
        forget_local_file(db, media, item, NOW)
        assert media.last_poll_result is None
        assert media.last_poll_message is None
    finally:
        db.close()


def test_voluntarily_deleted_episode_is_not_gap_filled(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(db, media)
        forget_local_file(db, media, item, NOW, voluntary=True)
        db.commit()
        assert is_voluntarily_deleted(item)
        slots = expected_slots(media, [item], today=TODAY, window_days=3, force=False)
        assert slots == []
    finally:
        db.close()


def test_unexpected_loss_marks_file_absent(client, app):
    del client
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series" / "Brume Port (2024)"
    video = series / "Brume.Port.S01E02.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    db = app.state.session_factory()
    try:
        media = _series(db)
        item = _episode(db, media, file_path=str(video.resolve()))
        db.commit()
        video.unlink()
        layout = storage_layout(db, app.state.settings)
        assert local_video_in(db, layout, item, NOW) is None
        db.commit()
        assert is_file_absent(item)
        assert not is_voluntarily_deleted(item)
        assert item.state == "missing"
    finally:
        db.close()


def test_cleanup_move_all_calls_shared_forget(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = _series(db, title="Cendre Lac")
        item = _episode(db, media, season=1, episode=1, title="Pilot")
        data = Path(app.state.settings.data_dir)
        series_root = data / "media" / "Series" / "Cendre Lac (2024)"
        path = series_root / "Cendre.Lac.S01E01.mkv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 20)
        item.file_path = str(path.resolve())
        db.commit()
        planned = PlannedFile(item=item, path=path, size=20)
        with patch("mediatracker.core.cleanup.forget_local_file") as forget:
            _move_all(db, app.state.settings, media, [planned], NOW, reason="auto", season=None)
            forget.assert_called()
            assert forget.call_args.kwargs.get("voluntary", False) is True
    finally:
        db.close()


def test_presence_calls_shared_forget(client, app):
    del client
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series" / "Rivage (2024)"
    video = series / "Rivage.S01E02.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Rivage", year=2024, tmdb_id=42)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=2,
            state="downloaded",
            downloaded=True,
            file_path=str(video.resolve()),
        )
        db.add(item)
        db.commit()
        video.unlink()
        layout = storage_layout(db, app.state.settings)
        with patch("mediatracker.core.presence.forget_local_file") as forget:
            local_video_in(db, layout, item, NOW)
            forget.assert_called_once()
            assert forget.call_args.kwargs.get("voluntary", False) is False
    finally:
        db.close()


def test_source_poll_reconcile_calls_shared_forget(client, app):
    del client
    data = Path(app.state.settings.data_dir)
    series = data / "media" / "Series" / "Lanterne (2025)"
    video = series / "Lanterne.S01E03.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"video")
    db = app.state.session_factory()
    try:
        media = Media(type="series", title="Lanterne", year=2025, tmdb_id=77)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=3,
            state="downloaded",
            downloaded=True,
            file_path=str(video.resolve()),
        )
        db.add(item)
        db.commit()
        video.unlink()
        with patch("mediatracker.core.source_poll.forget_local_file") as forget:
            refresh_library_claims(db, media, [item], app.state.settings, NOW)
            forget.assert_called_once()
            assert forget.call_args.kwargs.get("voluntary", False) is False
    finally:
        db.close()


def test_trace_kinds_are_stable():
    assert TRACE_DELETED_VOLUNTARILY == "supprimé_volontairement"
    assert TRACE_FILE_ABSENT == "fichier_absent"

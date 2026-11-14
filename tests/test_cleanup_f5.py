"""F5: classify au plus une fois par minute, sauf événement déclencheur."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from mediatracker.core.cleanup import classify as real_classify
from mediatracker.core.cleanup import delete_files, maintenance, restore
from mediatracker.core.cleanup_classify import reset_classify_schedule
from mediatracker.db.models import Media, MediaItem, TrashEntry
from mediatracker.integrations.jellyfin import JellyfinWatch
from mediatracker.integrations.jellyfin_sync import apply_played
from mediatracker.library.review import apply_merge, build_review


@pytest.fixture(autouse=True)
def _reset_classify_schedule():
    reset_classify_schedule()
    yield
    reset_classify_schedule()


def _count_classify(monkeypatch):
    calls: list[datetime] = []

    def _spy(db, settings, now):
        calls.append(now)
        return real_classify(db, settings, now)

    monkeypatch.setattr("mediatracker.core.cleanup.classify", _spy)
    return calls


def _media(db, **kwargs):
    row = Media(type=kwargs.pop("type", "film"), title=kwargs.pop("title", "Film"), **kwargs)
    db.add(row)
    db.flush()
    return row


def _file(root: Path, name: str, payload: bytes = b"video") -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


def _item(db, media, path: Path, *, season=None, episode=None, watched_at=None, watched=True):
    row = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title="épisode" if episode else media.title,
        state="downloaded",
        downloaded=True,
        downloaded_at=watched_at,
        watched=watched,
        watched_at=watched_at if watched else None,
        file_path=str(path),
    )
    db.add(row)
    db.flush()
    return row


def test_classify_at_most_once_per_minute_without_event(client, app, monkeypatch):
    del client
    calls = _count_classify(monkeypatch)
    base = datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        maintenance(db, app.state.settings, now=base)
        maintenance(db, app.state.settings, now=base + timedelta(seconds=30))
        maintenance(db, app.state.settings, now=base + timedelta(seconds=59))
        assert len(calls) == 1
        maintenance(db, app.state.settings, now=base + timedelta(seconds=60))
        assert len(calls) == 2
    finally:
        db.close()


def test_manual_trash_wakes_classify(client, app, monkeypatch):
    del client
    calls = _count_classify(monkeypatch)
    base = datetime(2026, 10, 22, 11, 0, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        path = _file(movies, "Brume/Brume.mkv", b"x" * 20)
        media = _media(db, title="Brume")
        _item(db, media, path, watched_at=base)
        db.commit()

        maintenance(db, app.state.settings, now=base)
        assert len(calls) == 1

        delete_files(
            db,
            app.state.settings,
            media_id=media.id,
            scope="media",
            season=None,
            item_id=None,
            now=base + timedelta(seconds=5),
        )
        maintenance(db, app.state.settings, now=base + timedelta(seconds=6))
        assert len(calls) == 2
    finally:
        db.close()


def test_restore_wakes_classify(client, app, monkeypatch):
    del client
    calls = _count_classify(monkeypatch)
    base = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        from mediatracker.core.storage import storage_layout

        trash = storage_layout(db, app.state.settings).trash_dir
        original = _file(movies, "Lueur/Lueur.mkv", b"z" * 10)
        media = _media(db, title="Lueur")
        item = _item(db, media, original, watched_at=base)
        original.unlink(missing_ok=True)
        trash_file = trash / "Lueur.mkv"
        trash_file.parent.mkdir(parents=True, exist_ok=True)
        trash_file.write_bytes(b"z" * 10)
        entry = TrashEntry(
            original_path=str(movies / "Lueur/Lueur.mkv"),
            trash_path=str(trash_file),
            size=10,
            media_id=media.id,
            media_item_id=item.id,
            label="Lueur",
            reason="manual",
            deleted_at=base,
            purge_after=base + timedelta(days=7),
        )
        db.add(entry)
        item.file_path = None
        item.downloaded = False
        db.commit()

        maintenance(db, app.state.settings, now=base)
        assert len(calls) == 1

        restore(db, app.state.settings, entry.id, now=base + timedelta(seconds=10))
        maintenance(db, app.state.settings, now=base + timedelta(seconds=11))
        assert len(calls) == 2
    finally:
        db.close()


def test_library_merge_wakes_classify(client, app, monkeypatch):
    del client
    calls = _count_classify(monkeypatch)
    base = datetime(2026, 10, 22, 13, 0, tzinfo=timezone.utc)
    data = Path(app.state.settings.data_dir) / "media"
    films = data / "Films"
    series = data / "Series"
    db = app.state.session_factory()
    try:
        tagged = series / "Vague (2020) [tmdbid-9]" / "Season 02"
        plain = series / "Vague (2020)" / "Season 02"
        tagged.mkdir(parents=True)
        plain.mkdir(parents=True)
        src = _file(plain, "Vague.S02E01.mkv", b"v" * 12)
        _file(tagged, "Vague.S02E02.mkv", b"v" * 11)
        media = _media(db, type="series", title="Vague", year=2020, tmdb_id=9)
        _item(db, media, src, season=2, episode=1, watched_at=base)
        db.commit()

        maintenance(db, app.state.settings, now=base)
        assert len(calls) == 1

        report = build_review(db, films, series)
        group = next(row for row in report.series if row.tmdb_id == 9)
        applied, _rename_id, _pending = apply_merge(db, films, series, group.key, validated=True)
        assert applied
        maintenance(db, app.state.settings, now=base + timedelta(seconds=4))
        assert len(calls) == 2
    finally:
        db.close()


def test_jellyfin_watched_wakes_classify(client, app, monkeypatch):
    del client
    calls = _count_classify(monkeypatch)
    base = datetime(2026, 10, 22, 14, 0, tzinfo=timezone.utc)
    movies = Path(app.state.settings.data_dir) / "media" / "Films"
    db = app.state.session_factory()
    try:
        path = _file(movies, "Zénith/Zénith.mkv", b"y" * 8)
        media = _media(db, title="Zénith", tmdb_id=42)
        item = _item(db, media, path, watched_at=None)
        item.watched = False
        item.watched_at = None
        db.commit()

        maintenance(db, app.state.settings, now=base)
        assert len(calls) == 1

        watch = JellyfinWatch(
            item_id="jf-1",
            item_type="Movie",
            tmdb_id=42,
            season=None,
            episode=None,
            path=str(path),
            played=True,
            user_id="u1",
        )
        apply_played(db, item, watch, base + timedelta(seconds=7))
        maintenance(db, app.state.settings, now=base + timedelta(seconds=8))
        assert len(calls) == 2
        assert db.scalar(select(MediaItem).where(MediaItem.id == item.id)).watched is True
    finally:
        db.close()

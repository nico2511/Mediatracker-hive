"""V6 — validation only: F10 guard, local-only fast paths, daily full scan, deletion at full scan."""

import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import login

from mediatracker.core.library_guard import LIBRARY_SUSPENDED_KEY
from mediatracker.core.worker_schedule import _future_deadlines
from mediatracker.db.models import Event, LibraryFile, Media, MediaItem, Setting
from mediatracker.library.inotify_watch import _register_watches
from mediatracker.library.scanner import LAST_SCAN_KEY, _touch_last_scan, maybe_scan_library, scan_library


class _Tmdb:
    def search(self, query: str, media_type: str):
        return []

    def fetch(self, tmdb_id: int, media_type: str):
        raise AssertionError("unexpected fetch")


NOW = datetime(2026, 10, 23, 8, tzinfo=timezone.utc)


def _layout(app):
    from mediatracker.core.storage import storage_layout

    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        return layout.movies_dir, layout.series_dir
    finally:
        db.close()


def _seed_film(db, movies: Path, title: str, *, on_disk: bool) -> Path:
    movies.mkdir(parents=True, exist_ok=True)
    media = Media(type="film", title=title, created_at=NOW)
    db.add(media)
    db.flush()
    folder = movies / title
    path = folder / f"{title}.mkv"
    if on_disk:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"clip")
    item = MediaItem(
        media_id=media.id,
        title=title,
        state="downloaded",
        downloaded=True,
        downloaded_at=NOW,
        file_path=str(path.resolve()),
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=str(path.resolve()),
            size=4,
            parsed_title=title,
            media_type="film",
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
    )
    db.commit()
    return path


def test_v6_f10_maybe_scan_empty_root_does_not_mass_mark_missing(client, app):
    """(a) Unmounted/empty library root must not mark every fiche missing."""
    login(client)
    movies, series = _layout(app)
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    db = app.state.session_factory()
    try:
        _seed_film(db, movies, "Brume Vide", on_disk=False)
        report = maybe_scan_library(db, app.state.settings, now=NOW, tmdb=_Tmdb())
        db.commit()
        assert report is not None
        assert report.missing == 0
        row = db.scalar(select(LibraryFile))
        assert row.status == "matched"
        assert row.missing_at is None
        assert db.scalar(select(Event).where(Event.type == "library.scan_suspended")) is not None
        assert db.get(Setting, LIBRARY_SUSPENDED_KEY).value == "1"
    finally:
        db.close()


def test_v6_incremental_and_inotify_only_on_local_fs(client, app, monkeypatch):
    """(b) Fast paths stay off when roots are not on a local filesystem."""
    del client
    films, series = _layout(app)
    show = series / "Distant (2019)"
    season = show / "Season 01"
    season.mkdir(parents=True)
    episode = season / "Distant - S01E01.mkv"
    episode.write_bytes(b"z")
    monkeypatch.setattr(
        "mediatracker.library.incremental.is_local_filesystem",
        lambda _path: False,
    )
    monkeypatch.setattr(
        "mediatracker.library.inotify_watch.is_local_filesystem",
        lambda _path: False,
    )
    db = app.state.session_factory()
    try:
        base = datetime(2026, 3, 3, tzinfo=timezone.utc)
        scan_library(db, movies=films, series=series, now=base, tmdb=_Tmdb(), settings=app.state.settings)
        _touch_last_scan(db, base)
        db.commit()
        os.utime(season, (base.timestamp() + 60, base.timestamp() + 60))
        assert (
            maybe_scan_library(
                db,
                app.state.settings,
                now=base + timedelta(hours=1),
                tmdb=_Tmdb(),
            )
            is None
        )
        watches = _register_watches(7, films, series, app.state.session_factory, app.state.settings)
        assert watches == {}
    finally:
        db.close()


def test_v6_daily_full_scan_stays_scheduled(client, app):
    """(c) Worker idle deadline still includes the next full library scan."""
    del client
    db = app.state.session_factory()
    try:
        last = datetime(2026, 10, 22, 6, 0, tzinfo=timezone.utc)
        db.merge(Setting(key=LAST_SCAN_KEY, value=last.isoformat(), is_secret=False, updated_at=last))
        db.merge(
            Setting(
                key="library_scan_interval_hours",
                value="24",
                is_secret=False,
                updated_at=last,
            )
        )
        db.commit()
        moment = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
        deadlines = _future_deadlines(db, app.state.settings, moment)
        assert last + timedelta(hours=24) in deadlines
    finally:
        db.close()


def test_v6_deleted_fiche_folder_detected_on_full_scan(client, app, monkeypatch):
    """(d) A removed show folder is reconciled at the latest on the periodic full scan."""
    login(client)
    movies, series = _layout(app)
    monkeypatch.setattr(
        "mediatracker.library.incremental.is_local_filesystem",
        lambda _path: True,
    )
    db = app.state.session_factory()
    try:
        paths = [_seed_film(db, movies, f"Ombre {index}", on_disk=True) for index in range(10)]
        base = datetime(2026, 4, 1, tzinfo=timezone.utc)
        scan_library(
            db,
            movies=movies,
            series=series,
            now=base,
            tmdb=_Tmdb(),
            settings=app.state.settings,
        )
        _touch_last_scan(db, base)
        db.commit()
        shutil.rmtree(paths[0].parent)
        path = paths[0]
        between = maybe_scan_library(
            db,
            app.state.settings,
            now=base + timedelta(hours=2),
            tmdb=_Tmdb(),
        )
        if between is not None:
            assert between.missing == 0
        gone = db.scalar(select(LibraryFile).where(LibraryFile.path == str(path.resolve())))
        assert gone.status == "matched"
        full = maybe_scan_library(
            db,
            app.state.settings,
            now=base + timedelta(hours=25),
            tmdb=_Tmdb(),
        )
        db.commit()
        assert full is not None
        assert full.missing == 1
        gone = db.scalar(select(LibraryFile).where(LibraryFile.path == str(path.resolve())))
        assert gone.status == "missing"
    finally:
        db.close()

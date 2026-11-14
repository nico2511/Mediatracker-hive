import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from mediatracker.db.models import Event, LibraryFolderMtime
from mediatracker.library.scanner import _touch_last_scan, force_scan, maybe_scan_library, scan_library


class _Tmdb:
    def search(self, query: str, media_type: str):
        return []

    def fetch(self, tmdb_id: int, media_type: str):
        raise AssertionError("unexpected fetch")


def _layout(data_dir: Path) -> tuple[Path, Path]:
    media = data_dir / "media"
    films = media / "Films"
    series = media / "Series"
    films.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    return films, series


def test_incremental_skips_unchanged_folders(client, app, monkeypatch):
    del client
    films, series = _layout(Path(app.state.settings.data_dir))
    show = series / "Faux Drame (2020)"
    season = show / "Season 01"
    season.mkdir(parents=True)
    episode = season / "Faux Drame - S01E01.mkv"
    episode.write_bytes(b"x")
    monkeypatch.setattr(
        "mediatracker.library.incremental.is_local_filesystem",
        lambda _path: True,
    )
    db = app.state.session_factory()
    try:
        now = datetime(2026, 3, 1, tzinfo=timezone.utc)
        force_scan(db, app.state.settings, now=now)
        rows = db.scalars(select(LibraryFolderMtime)).all()
        assert len(rows) >= 2
        again = maybe_scan_library(
            db,
            app.state.settings,
            now=now + timedelta(hours=1),
            tmdb=_Tmdb(),
        )
        assert again is None
    finally:
        db.close()


def test_incremental_rescans_when_folder_mtime_changes(client, app, monkeypatch):
    del client
    films, series = _layout(Path(app.state.settings.data_dir))
    show = series / "Autre Faux (2021)"
    season = show / "Season 01"
    season.mkdir(parents=True)
    episode = season / "Autre Faux - S01E01.mkv"
    episode.write_bytes(b"a")
    monkeypatch.setattr(
        "mediatracker.library.incremental.is_local_filesystem",
        lambda _path: True,
    )
    db = app.state.session_factory()
    try:
        base = datetime(2026, 3, 2, tzinfo=timezone.utc)
        force_scan(db, app.state.settings, now=base)
        episode.write_bytes(b"ab")
        os.utime(season, (base.timestamp() + 120, base.timestamp() + 120))
        report = maybe_scan_library(
            db,
            app.state.settings,
            now=base + timedelta(hours=2),
            tmdb=_Tmdb(),
        )
        assert report is not None
        assert report.seen >= 1
        event = db.scalar(select(Event).where(Event.type == "library.scanned").order_by(Event.id.desc()))
        assert event is not None
        assert event.payload.get("incremental") is True
    finally:
        db.close()


def test_incremental_disabled_on_non_local_root(client, app, monkeypatch):
    del client
    films, series = _layout(Path(app.state.settings.data_dir))
    show = series / "Distant (2019)"
    season = show / "Season 01"
    season.mkdir(parents=True)
    (season / "Distant - S01E01.mkv").write_bytes(b"z")
    monkeypatch.setattr(
        "mediatracker.library.incremental.is_local_filesystem",
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
    finally:
        db.close()

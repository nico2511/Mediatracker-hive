"""Startup pass: orphan .aria2 and empty files leave incomplete for the trash."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from mediatracker.core.incomplete import prune_incomplete_orphans_at_startup
from mediatracker.db.models import Download, Event, Media, MediaItem, Setting, TrashEntry

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)


def _layout(app):
    data = Path(app.state.settings.data_dir)
    return data / "downloads" / "incomplete", data / "media" / "Series", data / "media" / "Films"


class _Stopper:
    def __init__(self) -> None:
        self.names: list[str] = []

    def forget_files(self, paths) -> list[str]:
        self.names = [Path(path).name for path in paths]
        return ["gid-orphelin"]


def test_startup_trashes_orphan_aria2_and_empty_files(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    aria = incomplete / "Serie-Test.S01E01.mkv.aria2"
    aria.write_bytes(b"ctrl")
    empty = incomplete / "Serie-Demo.S01E02.mkv"
    empty.write_bytes(b"")
    stopper = _Stopper()

    def loader(db, settings, injected):
        del db, settings, injected
        return None, stopper

    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", loader)
    db = app.state.session_factory()
    try:
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert not aria.exists() and not empty.exists()
        assert {aria.name, empty.name} <= set(stopper.names)
        entries = list(db.scalars(select(TrashEntry)).all())
        assert len(entries) == 2
        event = db.scalar(select(Event).where(Event.type == "cleanup.incomplete_orphans"))
        assert event is not None
        assert event.payload["count"] == 2
        assert len(event.payload["paths"]) == 2
    finally:
        db.close()


def test_startup_keeps_file_for_active_job(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    active = incomplete / "Serie-Test.S01E03.mkv"
    active.write_bytes(b"")
    active_aria = incomplete / "Serie-Test.S01E03.mkv.aria2"
    active_aria.write_bytes(b"x")
    orphan = incomplete / "Serie-Test.S01E04.mkv.aria2"
    orphan.write_bytes(b"y")
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Serie-Test", year=2026)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=3, title="actif", state="downloading")
        db.add(item)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                state="downloading",
                filename="Serie-Test.S01E03.mkv",
                created_at=NOW,
            )
        )
        db.commit()
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert active.is_file() and active_aria.is_file()
        assert not orphan.exists()
        event = db.scalar(select(Event).where(Event.type == "cleanup.incomplete_orphans"))
        assert event is not None
        assert event.payload["count"] == 1
    finally:
        db.close()


def test_startup_skips_trash_nested_in_incomplete(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    nested_trash = incomplete / "corbeille"
    nested_trash.mkdir()
    already = nested_trash / "Serie-Demo.S01E01.mkv.aria2"
    already.write_bytes(b"old")
    orphan = incomplete / "Serie-Demo.S01E01.mkv.aria2"
    orphan.write_bytes(b"new")
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        row = db.get(Setting, "trash_dir")
        assert row is not None
        row.value = str(nested_trash)
        row.updated_at = NOW
        db.commit()
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert already.is_file()
        assert not orphan.exists()
        assert db.scalar(select(Event).where(Event.type == "cleanup.incomplete_orphans")) is not None
    finally:
        db.close()


def test_startup_trashes_recent_aria2_suffix_duplicate(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    collision = incomplete / "Serie-Demo.S01E01.mkv.1.mkv"
    collision.write_bytes(b"partial" * 500)
    fresh = datetime.now(timezone.utc)
    import os

    os.utime(collision, (fresh.timestamp(), fresh.timestamp()))
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert not collision.exists()
        entries = list(db.scalars(select(TrashEntry)).all())
        assert len(entries) == 1
        assert entries[0].reason == "incomplete"
    finally:
        db.close()


def test_startup_trashes_aria2_before_ext_duplicate_when_base_exists(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    base = incomplete / "Serie-Demo.S01E01.mkv"
    base.write_bytes(b"existing")
    collision = incomplete / "Serie-Demo.S01E01.1.mkv"
    collision.write_bytes(b"partial" * 300)
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert base.is_file()
        assert not collision.exists()
        assert len(db.scalars(select(TrashEntry)).all()) == 1
    finally:
        db.close()


def test_startup_keeps_before_ext_duplicate_without_base_or_job(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    lone = incomplete / "Serie-Test.S02E05.2.mkv"
    lone.write_bytes(b"segment" * 400)
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert lone.is_file()
        assert db.scalars(select(TrashEntry)).all() == []
    finally:
        db.close()


def test_startup_keeps_year_suffix_beside_base_mkv(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    base = incomplete / "Film-Test.mkv"
    base.write_bytes(b"main" * 50)
    by_year = incomplete / "Film-Test.2024.mkv"
    by_year.write_bytes(b"alt" * 50)
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert base.is_file() and by_year.is_file()
        assert db.scalars(select(TrashEntry)).all() == []
    finally:
        db.close()


def test_startup_keeps_before_ext_duplicate_while_base_job_active(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    base_name = "Serie-Test.S01E03.mkv"
    base = incomplete / base_name
    base.write_bytes(b"partial")
    collision = incomplete / "Serie-Test.S01E03.1.mkv"
    collision.write_bytes(b"partial" * 200)
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Serie-Test", year=2026)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=3, title="actif", state="downloading")
        db.add(item)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                state="downloading",
                filename=base_name,
                created_at=NOW,
            )
        )
        db.commit()
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert base.is_file() and collision.is_file()
        assert db.scalars(select(TrashEntry)).all() == []
    finally:
        db.close()


def test_startup_keeps_suffix_duplicate_for_active_job(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    name = "Serie-Test.S01E03.mkv.1.mkv"
    collision = incomplete / name
    collision.write_bytes(b"partial" * 500)
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        show = Media(type="series", title="Serie-Test", year=2026)
        db.add(show)
        db.flush()
        item = MediaItem(media_id=show.id, season=1, episode=3, title="actif", state="downloading")
        db.add(item)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                state="downloading",
                filename=name,
                created_at=NOW,
            )
        )
        db.commit()
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert collision.is_file()
        assert db.scalars(select(TrashEntry)).all() == []
    finally:
        db.close()


def test_startup_keeps_nonempty_video_without_aria2(client, app, monkeypatch):
    incomplete, _series, _movies = _layout(app)
    incomplete.mkdir(parents=True, exist_ok=True)
    kept = incomplete / "Film-Test.Demo.2026.mkv"
    kept.write_bytes(b"video" * 100)
    stray = incomplete / "Film-Test.Demo.2026.mkv.aria2"
    stray.write_bytes(b"z")
    monkeypatch.setattr("mediatracker.core.pipeline.clients._downloader", lambda *a, **k: (None, None))
    db = app.state.session_factory()
    try:
        prune_incomplete_orphans_at_startup(db, app.state.settings)
        assert kept.is_file()
        assert not stray.exists()
        event = db.scalar(select(Event).where(Event.type == "cleanup.incomplete_orphans"))
        assert event is not None
        assert event.payload["count"] == 1
        assert Path(event.payload["paths"][0]).name == stray.name
    finally:
        db.close()

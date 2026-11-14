"""F10: suspend library absence updates when the volume looks empty or mostly gone."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import login

from mediatracker.core.forget import TRACE_FILE_ABSENT
from mediatracker.core.library_guard import LIBRARY_SUSPENDED_KEY, MASS_ABSENCE_FRACTION
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import Event, LibraryFile, Media, MediaItem, Setting, Webhook, WebhookDelivery
from mediatracker.events.deliver import deliver_due
from mediatracker.library.scanner import scan_library

NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)


def _layout(app):
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        return layout.movies_dir, layout.series_dir
    finally:
        db.close()


def _seed_rows(db, movies: Path, count: int, *, prefix: str, on_disk: bool) -> list[Path]:
    movies.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index in range(count):
        media = Media(type="film", title=f"{prefix}-{index}", created_at=NOW)
        db.add(media)
        db.flush()
        folder = movies / f"{prefix}-{index}"
        path = folder / f"{prefix}-{index}.mkv"
        if on_disk:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"clip")
        item = MediaItem(
            media_id=media.id,
            title=f"{prefix}-{index}",
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
                parsed_title=f"{prefix}-{index}",
                media_type="film",
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        paths.append(path)
    db.commit()
    return paths


def test_empty_root_suspends_without_marking_missing(client, app):
    login(client)
    movies, series = _layout(app)
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    db = app.state.session_factory()
    try:
        _seed_rows(db, movies, 1, prefix="Brume", on_disk=False)
        report = scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        assert report.missing == 0
        row = db.scalar(select(LibraryFile))
        assert row.status == "matched"
        assert row.missing_at is None
        event = db.scalar(select(Event).where(Event.type == "library.scan_suspended"))
        assert event is not None
        assert event.payload["reason"] == "empty_root"
        flag = db.get(Setting, LIBRARY_SUSPENDED_KEY)
        assert flag is not None and flag.value == "1"
    finally:
        db.close()


def test_twenty_five_percent_disappeared_is_suspended(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        paths = _seed_rows(db, movies, 4, prefix="Cristal", on_disk=True)
        paths[0].unlink()
        report = scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        assert report.missing == 0
        assert MASS_ABSENCE_FRACTION == 0.20
        rows = list(db.scalars(select(LibraryFile)))
        assert all(row.status == "matched" for row in rows)
        event = db.scalar(select(Event).where(Event.type == "library.scan_suspended"))
        assert event is not None
        assert event.payload["reason"] == "mass_absence"
        assert event.payload["known"] == 4
        assert event.payload["absent"] == 1
    finally:
        db.close()


def test_ten_percent_disappeared_still_marks_missing(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        paths = _seed_rows(db, movies, 10, prefix="Delta", on_disk=True)
        paths[0].unlink()
        report = scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        assert report.missing == 1
        gone = db.scalar(select(LibraryFile).where(LibraryFile.path == str(paths[0].resolve())))
        assert gone.status == "missing"
        assert db.scalar(select(Event).where(Event.type == "library.scan_suspended")) is None
    finally:
        db.close()


def test_suspend_notifies_home_assistant_once(client, app, monkeypatch):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        _seed_rows(db, movies, 2, prefix="Echo", on_disk=False)
        db.add(
            Webhook(
                name="ha",
                url="http://127.0.0.1:9/ha",
                type="homeassistant",
                enabled=True,
                events=[],
            )
        )
        db.commit()
    finally:
        db.close()
    calls: list[bytes] = []

    def send(url, body, headers):
        del url, headers
        calls.append(body)
        return 200

    monkeypatch.setattr("mediatracker.events.deliver.post_request", send)
    db = app.state.session_factory()
    try:
        scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        suspended = db.scalar(select(Event).where(Event.type == "library.scan_suspended"))
        assert suspended is not None
        delivery = db.scalar(
            select(WebhookDelivery).where(WebhookDelivery.event_id == suspended.id)
        )
        assert delivery is not None
        assert deliver_due(db, app.state.settings, NOW) == 1
        assert len(calls) == 1
        scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        assert db.query(Event).filter(Event.type == "library.scan_suspended").count() == 1
        assert db.query(WebhookDelivery).count() == 1
        assert deliver_due(db, app.state.settings, NOW) == 0
        assert len(calls) == 1
    finally:
        db.close()


def test_resume_on_first_normal_scan(client, app):
    login(client)
    movies, series = _layout(app)
    db = app.state.session_factory()
    try:
        paths = _seed_rows(db, movies, 4, prefix="Fable", on_disk=True)
        paths[0].unlink()
        scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        assert db.scalar(select(Event).where(Event.type == "library.scan_suspended")) is not None
        paths[0].parent.mkdir(parents=True, exist_ok=True)
        paths[0].write_bytes(b"back")
        report = scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
        assert report.missing == 0
        flag = db.get(Setting, LIBRARY_SUSPENDED_KEY)
        assert flag is not None and flag.value == "0"
        assert db.scalar(select(Event).where(Event.type == "library.scan_resumed")) is not None
        library = db.scalar(select(LibraryFile).where(LibraryFile.path == str(paths[0].resolve())))
        item = db.get(MediaItem, library.media_item_id)
        traces = item.traces or []
        assert not any(entry.get("kind") == TRACE_FILE_ABSENT for entry in traces)
    finally:
        db.close()


def test_health_marks_library_degraded_while_suspended(client, app):
    login(client)
    movies, series = _layout(app)
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    db = app.state.session_factory()
    try:
        _seed_rows(db, movies, 1, prefix="Givre", on_disk=False)
        scan_library(db, movies=movies, series=series, now=NOW, tmdb=None)
        db.commit()
    finally:
        db.close()
    body = client.get("/api/v1/health/components").json()
    library = next(row for row in body["components"] if row["name"] == "library")
    assert library["status"] == "degraded"

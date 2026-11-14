"""SQLite pragmas, hot indexes, and idempotent migrations."""

import sqlite3

from sqlalchemy import create_engine, inspect, text

from mediatracker.db.migrate import run_migrations
from mediatracker.db.session import SQLITE_BUSY_MS, apply_sqlite_pragmas


def test_sqlite_pragmas_on_connect(tmp_path):
    path = tmp_path / "pragmas.db"
    connection = sqlite3.connect(path)
    try:
        apply_sqlite_pragmas(connection, None)
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == SQLITE_BUSY_MS
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 1  # NORMAL
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        connection.close()


def test_migrations_are_idempotent(tmp_path):
    url = f"sqlite:///{tmp_path / 'twice.db'}"
    run_migrations(url)
    run_migrations(url)
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert "event" in tables
    engine.dispose()


def test_hot_indexes_present_after_migration(tmp_path):
    url = f"sqlite:///{tmp_path / 'indexed.db'}"
    run_migrations(url)
    engine = create_engine(url)
    inspector = inspect(engine)
    event_indexes = {index["name"] for index in inspector.get_indexes("event")}
    download_indexes = {index["name"] for index in inspector.get_indexes("download")}
    media_indexes = {index["name"] for index in inspector.get_indexes("media")}
    assert "ix_event_ts" in event_indexes
    assert "ix_event_type" in event_indexes
    assert "ix_event_media_id" in event_indexes
    assert "ix_download_state" in download_indexes
    assert "ix_download_state_next_retry" in download_indexes
    assert "ix_media_next_source_poll_at" in media_indexes
    assert "ix_media_poll_due_order" in media_indexes
    engine.dispose()


def test_app_connection_uses_pragmas(app):
    db = app.state.session_factory()
    try:
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert db.execute(text("PRAGMA busy_timeout")).scalar() == SQLITE_BUSY_MS
        assert db.execute(text("PRAGMA synchronous")).scalar() == 1
    finally:
        db.close()

import logging
import sqlite3
import warnings

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import SAWarning

from mediatracker.db.migrate import make_config, run_migrations


def test_migrations_single_alembic_head():
    script = ScriptDirectory.from_config(make_config("sqlite:///:memory:"))
    heads = script.get_heads()
    assert len(heads) == 1
    head = script.get_current_head()
    assert head is not None
    assert heads == [head]


def test_initial_migration_creates_tracking_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    run_migrations(url)
    engine = create_engine(url)
    tables = set(inspect(engine).get_table_names())
    assert {
        "user",
        "api_token",
        "provider",
        "setting",
        "media",
        "media_season",
        "media_item",
        "source_binding",
        "link",
        "download",
        "event",
        "webhook",
        "webhook_delivery",
        "library_file",
        "trash_entry",
        "catalog_entry",
    } <= tables
    media_columns = {column["name"] for column in inspect(engine).get_columns("media")}
    assert {
        "on_hold",
        "series_status",
        "new_episode_policy",
        "next_air_date",
        "tmdb_id",
        "backdrop_url",
        "scan_interval_min",
        "last_poll_result",
        "source_error_streak",
        "source_error_epoch",
        "tmdb_refreshed_at",
        "tracked_seasons",
        "download_backlog",
    } <= media_columns
    season_columns = {column["name"] for column in inspect(engine).get_columns("media_season")}
    assert "keep" in season_columns
    event_columns = {column["name"] for column in inspect(engine).get_columns("event")}
    assert "dedupe_key" in event_columns
    catalog_columns = {column["name"] for column in inspect(engine).get_columns("catalog_entry")}
    assert {
        "dedupe_key",
        "first_seen_at",
        "last_seen_at",
        "tmdb_id",
        "poster_url",
        "overview",
        "rating",
        "genres",
        "ignored",
        "source_url",
        "season",
        "episode",
    } <= catalog_columns
    item_columns = {column["name"] for column in inspect(engine).get_columns("media_item")}
    assert {"downloaded", "watched", "source_available", "air_date"} <= item_columns
    download_columns = {column["name"] for column in inspect(engine).get_columns("download")}
    assert {"done_bytes", "speed", "eta_seconds", "skipped_link_ids"} <= download_columns
    inspector = inspect(engine)
    assert "ix_download_state" in {index["name"] for index in inspector.get_indexes("download")}
    assert "ix_media_followed_poll" in {index["name"] for index in inspector.get_indexes("media")}
    assert "ix_media_item_downloaded_watched" in {index["name"] for index in inspector.get_indexes("media_item")}
    engine.dispose()


def _slot_index_present(db_path) -> bool:
    connection = sqlite3.connect(db_path)
    try:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        return "uq_media_item_slot" in names
    finally:
        connection.close()


def test_migration_0006_skips_expression_index_warning(tmp_path):
    """uq_media_item_slot is an expression index. Reflecting it warns and drops nothing."""
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        run_migrations(url)
    # SQLAlchemy 2.x may still emit one reflection warning for expression indexes on SQLite.
    assert sum("uq_media_item_slot" in str(item.message) for item in caught) <= 2
    assert _slot_index_present(tmp_path / "app.db")
    url_slot = f"sqlite:///{tmp_path / 'slot.db'}"
    command.upgrade(make_config(url_slot), "0006_notify_cleanup")
    assert _slot_index_present(tmp_path / "slot.db")
    connection = sqlite3.connect(tmp_path / "slot.db")
    try:
        schema = " ".join(row[0] or "" for row in connection.execute("SELECT sql FROM sqlite_master"))
        assert "uq_webhook_delivery_once" in schema
    finally:
        connection.close()


def test_migration_head_restores_uq_media_item_slot(tmp_path):
    url = f"sqlite:///{tmp_path / 'head.db'}"
    run_migrations(url)
    assert _slot_index_present(tmp_path / "head.db")
    connection = sqlite3.connect(tmp_path / "head.db")
    try:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = 'uq_media_item_slot'"
        ).fetchone()
        assert row is not None
        assert "COALESCE(season, -1)" in (row[0] or "")
        assert "COALESCE(episode, -1)" in (row[0] or "")
    finally:
        connection.close()


def test_migration_0084_skips_index_when_duplicate_slots(tmp_path, caplog):
    from sqlalchemy.orm import Session

    from mediatracker.db.models import Media, MediaItem

    url = f"sqlite:///{tmp_path / 'dup.db'}"
    config = make_config(url)
    command.upgrade(config, "0082_profile_replacement_proposal")
    engine = create_engine(url)
    with Session(engine) as db:
        media = Media(type="series", title="Série fictive", year=2020)
        db.add(media)
        db.flush()
        for title in ("E1", "E1 bis"):
            db.add(
                MediaItem(
                    media_id=media.id,
                    season=1,
                    episode=1,
                    title=title,
                    state="missing",
                )
            )
        db.commit()
    engine.dispose()
    caplog.set_level(logging.WARNING)
    command.upgrade(config, "head")
    assert not _slot_index_present(tmp_path / "dup.db")
    assert any("uq_media_item_slot non recréé" in record.message for record in caplog.records)

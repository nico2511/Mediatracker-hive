import sqlite3
import warnings

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


def test_migration_0006_skips_expression_index_warning(tmp_path):
    """uq_media_item_slot is an expression index. Reflecting it warns and drops nothing."""
    url = f"sqlite:///{tmp_path / 'app.db'}"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", SAWarning)
        run_migrations(url)
    assert not any("uq_media_item_slot" in str(item.message) for item in caught)
    connection = sqlite3.connect(tmp_path / "app.db")
    try:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "uq_media_item_slot" in names
        schema = " ".join(row[0] or "" for row in connection.execute("SELECT sql FROM sqlite_master"))
        assert "uq_webhook_delivery_once" in schema
    finally:
        connection.close()

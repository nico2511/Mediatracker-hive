"""Default download source order and migration from the historical pair."""

from mediatracker.config import Settings
from mediatracker.core.selection import (
    DEFAULT_DOWNLOAD_SOURCE_ORDER,
    DOWNLOAD_SOURCE_ORDER_WITH_UWUPLUS,
    LEGACY_DOWNLOAD_SOURCE_ORDER,
    parse_download_source_order,
)
from mediatracker.db.models import Setting
from mediatracker.db.session import session_scope
from mediatracker.providers.sources.stremio_addons_config import replace_uwuplus_in_source_order
from mediatracker.settings_store import ensure_settings, migrate_download_source_order_setting


def test_default_download_source_order_matches_registry():
    expected = "wawacity,hive,comet,torrentio,stremio_addons"
    assert DEFAULT_DOWNLOAD_SOURCE_ORDER == expected
    assert Settings().download_source_order == expected
    assert parse_download_source_order(DEFAULT_DOWNLOAD_SOURCE_ORDER) == [
        "wawacity",
        "hive",
        "comet",
        "torrentio",
        "stremio_addons",
    ]


OLD_FACTORY_DOWNLOAD_SOURCE_ORDER = (
    "wawacity,free_telecharger,hive,comet,torrentio,stremio_addons"
)


def test_migration_keeps_old_factory_default_with_free_telecharger(client, app):
    del client
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = OLD_FACTORY_DOWNLOAD_SOURCE_ORDER
        db.commit()
        migrate_download_source_order_setting(db)
        db.commit()
        db.refresh(row)
        assert row.value == OLD_FACTORY_DOWNLOAD_SOURCE_ORDER


def test_replace_uwuplus_in_order_keeps_stremio_position():
    before = ["comet", "uwuplus", "stremio_addons", "wawacity"]
    assert replace_uwuplus_in_source_order(before) == ["comet", "stremio_addons", "wawacity"]


def test_replace_uwuplus_inserts_stremio_at_same_slot():
    before = ["wawacity", "uwuplus", "comet"]
    assert replace_uwuplus_in_source_order(before) == ["wawacity", "stremio_addons", "comet"]


def test_migration_replaces_legacy_default_only(client, app):
    del client
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = LEGACY_DOWNLOAD_SOURCE_ORDER
        db.commit()
        migrate_download_source_order_setting(db)
        db.commit()
        db.refresh(row)
        assert row.value == DEFAULT_DOWNLOAD_SOURCE_ORDER
        migrate_download_source_order_setting(db)
        db.commit()
        db.refresh(row)
        assert row.value == DEFAULT_DOWNLOAD_SOURCE_ORDER


def test_migration_replaces_default_with_uwuplus(client, app):
    del client
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = DOWNLOAD_SOURCE_ORDER_WITH_UWUPLUS
        db.commit()
        migrate_download_source_order_setting(db)
        db.commit()
        db.refresh(row)
        assert row.value == DEFAULT_DOWNLOAD_SOURCE_ORDER


def test_migration_keeps_custom_order(client, app):
    del client
    custom = "comet,wawacity,torrentio"
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = custom
        db.commit()
        migrate_download_source_order_setting(db)
        db.commit()
        db.refresh(row)
        assert row.value == custom


def test_migration_keeps_explicit_empty(client, app):
    del client
    settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = ""
        db.commit()
        migrate_download_source_order_setting(db)
        ensure_settings(db, settings)
        db.refresh(row)
        assert (row.value or "").strip() == ""

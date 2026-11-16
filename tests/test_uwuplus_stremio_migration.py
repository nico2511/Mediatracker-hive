"""Migration UwU+ dédié → addon dans stremio_addons."""

import json

from sqlalchemy import select

from mediatracker.config import Settings
from mediatracker.db.models import Provider, Setting
from mediatracker.db.session import session_scope
from mediatracker.providers.sources.stremio_addons_config import (
    migrate_uwuplus_into_stremio_addons,
    parse_stremio_addons_setting,
    resolve_stremio_addons,
)
from mediatracker.settings_store import ensure_settings, migrate_download_source_order_setting

MIGRATED_URL = "https://addon-alpha.example"
EXISTING = "https://addon-beta.example"
MIGRATED_KEY = "system.uwuplus_stremio_migrated"


def _reset_uwu_migration(db) -> None:
    row = db.get(Setting, MIGRATED_KEY)
    if row is not None:
        db.delete(row)
        db.flush()


def test_migrate_uwuplus_url_into_stremio_addons(client, app):
    del client
    settings: Settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        db.add(
            Setting(
                key="uwuplus_base_url",
                value=MIGRATED_URL,
                is_secret=False,
            )
        )
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = "wawacity,uwuplus,comet"
        _reset_uwu_migration(db)
        db.commit()
        migrate_uwuplus_into_stremio_addons(db, settings)
        migrate_download_source_order_setting(db)
        db.commit()
        migrated = resolve_stremio_addons(db, settings)
        assert len(migrated) == 1
        assert migrated[0].url == MIGRATED_URL
        assert migrated[0].enabled is True
        uwu_row = db.get(Setting, "uwuplus_base_url")
        assert uwu_row is not None
        assert (uwu_row.value or "").strip() == ""
        order = db.get(Setting, "download_source_order")
        assert order is not None
        assert order.value == "wawacity,stremio_addons,comet"


def test_migrate_skips_duplicate_addon_url(client, app):
    del client
    settings: Settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        payload = json.dumps([{"url": MIGRATED_URL, "enabled": True}])
        db.add(Setting(key="stremio_addons", value=payload, is_secret=False))
        db.add(Setting(key="uwuplus_base_url", value=MIGRATED_URL, is_secret=False))
        _reset_uwu_migration(db)
        db.commit()
        migrate_uwuplus_into_stremio_addons(db, settings)
        db.commit()
        addons = parse_stremio_addons_setting(db.get(Setting, "stremio_addons").value)
        assert len(addons) == 1
        assert addons[0].url == MIGRATED_URL


def test_migrate_leaves_addons_empty_when_no_uwu_url(client, app):
    del client
    settings: Settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        ensure_settings(db, settings)
        row = db.get(Setting, "stremio_addons")
        assert row is None or not (row.value or "").strip()


def test_migrate_order_replaces_uwu_without_duplicating_stremio(client, app):
    del client
    with session_scope(app.state.session_factory) as db:
        row = db.get(Setting, "download_source_order")
        assert row is not None
        row.value = "comet,uwuplus,stremio_addons,wawacity"
        db.commit()
        migrate_download_source_order_setting(db)
        db.commit()
        assert row.value == "comet,stremio_addons,wawacity"


def test_migrate_disables_legacy_uwuplus_provider(client, app):
    del client
    settings: Settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        db.add(
            Provider(
                kind="source",
                type="uwuplus",
                name="Legacy",
                enabled=True,
                priority=0,
                config={"base_url": EXISTING},
                builtin=True,
            )
        )
        _reset_uwu_migration(db)
        db.commit()
        migrate_uwuplus_into_stremio_addons(db, settings)
        db.commit()
        provider = db.scalar(select(Provider).where(Provider.type == "uwuplus"))
        assert provider is None


def test_migrate_retires_uwuplus_provider_when_already_migrated(client, app):
    del client
    settings: Settings = app.state.settings
    with session_scope(app.state.session_factory) as db:
        db.add(
            Provider(
                kind="source",
                type="uwuplus",
                name="Legacy",
                enabled=False,
                priority=0,
                config={},
                builtin=True,
            )
        )
        row = db.get(Setting, MIGRATED_KEY)
        if row is None:
            db.add(Setting(key=MIGRATED_KEY, value="1", is_secret=False))
        else:
            row.value = "1"
        db.commit()
        migrate_uwuplus_into_stremio_addons(db, settings)
        db.commit()
        assert db.scalar(select(Provider).where(Provider.type == "uwuplus")) is None
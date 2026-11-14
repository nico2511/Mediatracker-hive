"""Hive : partage catalogue manuel par défaut."""

from __future__ import annotations

from datetime import datetime, timezone

from alembic import command
from sqlalchemy import func, select
from tests.conftest import csrf
from tests.test_hive_catalog_sharing_worker import _enable_hive, _join_ruche, _origin_id
from tests.test_hive_sharing_catalog_entry_local import _film_with_file

from mediatracker.db.models import HiveEntry, HiveFileFingerprint, HiveShareIntent, MediaItem, Setting
from mediatracker.hive.catalog import sync_hive_catalog
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.share_policy import (
    HIVE_CATALOG_AUTO_SHARE_KEY,
    abandon_orphan_fingerprint_work,
    ensure_hive_catalog_auto_share_default,
    grant_share_intent,
    hive_catalog_auto_share_enabled,
)

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


def test_default_manual_no_auto_share_setting(client, app):
    db = app.state.session_factory()
    try:
        ensure_hive_catalog_auto_share_default(db)
        db.commit()
        ensure_hive_catalog_auto_share_default(db)
        db.commit()
        assert hive_catalog_auto_share_enabled(db) is False
        row = db.get(Setting, HIVE_CATALOG_AUTO_SHARE_KEY)
        assert row is not None and row.value == "false"
    finally:
        db.close()


def test_scan_without_intent_skips_fingerprint_and_catalog(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xD0] * 15 + [1])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=89001, write_hash=False)
        item_id = item.id
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.flush()
        assert db.get(HiveFileFingerprint, item_id) is None
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert db.scalar(select(func.count()).select_from(HiveEntry)) == 0
    finally:
        db.close()


def test_manual_patch_enables_share_and_fingerprint(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xD1] * 15 + [2])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=89002, write_hash=False)
        item_id = item.id
        db.commit()
    finally:
        db.close()
    patch = client.patch(
        f"/api/v1/hive/items/{item_id}/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert patch.status_code == 204, patch.text
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, item_id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        assert db.get(HiveFileFingerprint, item_id) is not None
        assert db.get(HiveShareIntent, item_id) is not None
        for _ in range(64):
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            fp = db.get(HiveFileFingerprint, item_id)
            if fp and fp.sha256_hex:
                break
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        row = db.scalar(
            select(HiveEntry).where(HiveEntry.media_item_id == item_id, HiveEntry.etat == "disponible").limit(1)
        )
        assert row is not None
    finally:
        db.close()


def test_already_shared_entry_preserved_without_tombstone(client, app, settings):
    """Avec intention, l'entrée disponible reste après sync sans retrait."""
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xD2] * 15 + [3])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=89003, write_hash=True)
        grant_share_intent(db, item.id)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        before = db.scalar(select(func.count()).select_from(HiveEntry).where(HiveEntry.etat == "disponible"))
        assert before and int(before) >= 1
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        after = db.scalar(select(func.count()).select_from(HiveEntry).where(HiveEntry.etat == "disponible"))
        assert after == before
        assert db.get(HiveShareIntent, item.id) is not None
    finally:
        db.close()


def test_abandon_orphan_in_progress_fingerprint(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=89004, write_hash=False)
        fp = HiveFileFingerprint(
            media_item_id=item.id,
            path=item.file_path or "",
            size=8,
            mtime_ns=1,
            partial_size=0,
            updated_at=NOW,
        )
        db.add(fp)
        db.commit()
        removed = abandon_orphan_fingerprint_work(db)
        db.commit()
        assert removed == 1
        assert db.get(HiveFileFingerprint, item.id) is None
    finally:
        db.close()


def test_auto_share_setting_restores_global_sync(client, app, settings):
    _enable_hive(client)
    origin = _origin_id(settings)
    ruche_id = bytes([0xD3] * 15 + [4])
    db = app.state.session_factory()
    try:
        _join_ruche(db, origin, ruche_id, settings)
        _, item = _film_with_file(db, app, settings, tmdb_id=89005, write_hash=True)
        db.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="true"))
        db.commit()
        assert hive_catalog_auto_share_enabled(db)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        row = db.scalar(select(HiveEntry).where(HiveEntry.media_item_id == item.id).limit(1))
        assert row is not None and row.etat == "disponible"
    finally:
        db.close()


def test_migration_idempotent_grants_intent_for_published(tmp_path):
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session

    from mediatracker.db.migrate import make_config
    from mediatracker.db.models import Media

    url = f"sqlite:///{tmp_path / 'm0057.db'}"
    cfg = make_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0056_download_poll_profile")
    engine = create_engine(url)
    session = Session(engine)
    try:
        session.execute(
            text(
                "INSERT INTO media (type, title, year, tmdb_id, followed, follow_origin, auto_download, "
                "pref_quality, pref_language, allow_fallback, on_hold, catch_up_pending, "
                "new_episode_policy, download_backlog, created_at) "
                "VALUES ('film', 'Mig', 2099, 89100, 1, 'user', 0, 'HD', 'VFF', "
                "0, 0, 0, 'propose', 0, CURRENT_TIMESTAMP)"
            )
        )
        media_id = session.execute(text("SELECT last_insert_rowid()")).scalar_one()
        session.execute(
            text(
                "INSERT INTO media_item (media_id, downloaded, state) VALUES (:mid, 1, 'downloaded')"
            ),
            {"mid": media_id},
        )
        item_id = session.execute(text("SELECT last_insert_rowid()")).scalar_one()
        session.execute(
            text(
                "INSERT INTO hive_entry (ruche_id, origin_instance_id, seq, etat, media_item_id, created_at) "
                "VALUES (:rid, :origin, 1, 'disponible', :iid, CURRENT_TIMESTAMP)"
            ),
            {"rid": b"\x01" * 16, "origin": "hv1test-test-test-test-test-test", "iid": item_id},
        )
        session.execute(
            text(
                "INSERT INTO hive_file_fingerprint (media_item_id, path, size, mtime_ns, partial_size, updated_at) "
                "VALUES (:iid, '/tmp/orphan.mkv', 1, 1, 0, :ts)"
            ),
            {"iid": item_id, "ts": NOW},
        )
        session.commit()
    finally:
        session.close()

    command.upgrade(cfg, "0057_hive_manual_share_default")
    command.upgrade(cfg, "0057_hive_manual_share_default")

    command.upgrade(cfg, "head")

    session = Session(engine)
    try:
        assert session.get(Setting, HIVE_CATALOG_AUTO_SHARE_KEY) is not None
        assert session.get(HiveShareIntent, item_id) is not None
        media2 = Media(type="film", title="Orphan", year=2098, tmdb_id=89101)
        session.add(media2)
        session.flush()
        orphan = MediaItem(media_id=media2.id, downloaded=True)
        session.add(orphan)
        session.flush()
        session.add(
            HiveFileFingerprint(
                media_item_id=orphan.id,
                path="/tmp/nointent.mkv",
                size=1,
                mtime_ns=1,
                partial_size=0,
                updated_at=NOW,
            )
        )
        session.commit()
        abandon_orphan_fingerprint_work(session)
        session.commit()
        assert session.get(HiveFileFingerprint, orphan.id) is None
    finally:
        session.close()

"""Réévaluation des entrées de file d'empreintes Hive en échec (option B)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from tests.test_hive_catalog_sharing_worker import _enable_hive
from tests.test_hive_sharing_catalog_entry_local import _film_with_file

from mediatracker.db.models import HiveFileFingerprint, HiveFingerprintQueue
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item
from mediatracker.hive.fingerprint_queue import (
    backfill_queue_from_incomplete,
    mark_queue_failed,
)
from mediatracker.hive.share_policy import grant_share_intent

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _seed_incomplete_failed(db, app, settings, *, tmdb_id: int, fail_reason: str, updated_at):
    _, item = _film_with_file(db, app, settings, tmdb_id=tmdb_id, write_hash=False)
    grant_share_intent(db, item.id)
    ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
    mark_queue_failed(db, item.id, reason=fail_reason, now=updated_at)
    row = db.scalar(
        select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id)
    )
    assert row is not None and row.status == "failed"
    row.updated_at = updated_at
    db.flush()
    return item


def test_backfill_leaves_recent_failed_blocked(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        item = _seed_incomplete_failed(
            db,
            app,
            settings,
            tmdb_id=98101,
            fail_reason="Fichier local absent.",
            updated_at=NOW - timedelta(hours=2),
        )
        added = backfill_queue_from_incomplete(db, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveFingerprintQueue).where(
                HiveFingerprintQueue.media_item_id == item.id
            )
        )
        assert added == 0
        assert row is not None
        assert row.status == "failed"
    finally:
        db.close()


def test_backfill_retries_failed_after_one_day(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        item = _seed_incomplete_failed(
            db,
            app,
            settings,
            tmdb_id=98102,
            fail_reason="Fichier local absent.",
            updated_at=NOW - timedelta(hours=25),
        )
        added = backfill_queue_from_incomplete(db, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveFingerprintQueue).where(
                HiveFingerprintQueue.media_item_id == item.id
            )
        )
        assert added == 1
        assert row is not None
        assert row.status == "pending"
        assert row.fail_reason is None
    finally:
        db.close()


def test_backfill_never_retries_trash_failed(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        item = _seed_incomplete_failed(
            db,
            app,
            settings,
            tmdb_id=98103,
            fail_reason="Fichier en corbeille.",
            updated_at=NOW - timedelta(days=3),
        )
        added = backfill_queue_from_incomplete(db, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveFingerprintQueue).where(
                HiveFingerprintQueue.media_item_id == item.id
            )
        )
        assert added == 0
        assert row is not None
        assert row.status == "failed"
    finally:
        db.close()


def test_ensure_fingerprint_after_failed_reenqueues_on_present_file(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        item = _seed_incomplete_failed(
            db,
            app,
            settings,
            tmdb_id=98104,
            fail_reason="Ouverture fichier impossible.",
            updated_at=NOW - timedelta(hours=1),
        )
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None and not (fp.sha256_hex or "").strip()
        item = db.get(type(item), item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.flush()
        row = db.scalar(
            select(HiveFingerprintQueue).where(
                HiveFingerprintQueue.media_item_id == item.id
            )
        )
        assert row is not None
        assert row.status == "pending"
    finally:
        db.close()


def test_backfill_does_not_duplicate_queue_row(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        item = _seed_incomplete_failed(
            db,
            app,
            settings,
            tmdb_id=98105,
            fail_reason="Fichier local absent.",
            updated_at=NOW - timedelta(hours=30),
        )
        backfill_queue_from_incomplete(db, now=NOW)
        backfill_queue_from_incomplete(db, now=NOW + timedelta(seconds=1))
        db.flush()
        n = db.scalar(
            select(func.count())
            .select_from(HiveFingerprintQueue)
            .where(HiveFingerprintQueue.media_item_id == item.id)
        )
        assert int(n or 0) == 1
    finally:
        db.close()
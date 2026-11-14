"""Fiabilisation worker empreintes Hive : reprise, progression, réveil, journal."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from tests.test_hive_catalog_sharing_worker import _enable_hive
from tests.test_hive_sharing_catalog_entry_local import _film_with_file

from mediatracker.core.worker_wake import REASON_HIVE_FINGERPRINT, reset_wake_metrics, wake_counts
from mediatracker.db.models import Event, HiveFileFingerprint
from mediatracker.hive.fingerprint import (
    HASH_CLAIM_STALE,
    ensure_fingerprint_row_for_item,
    hive_fingerprint_tick,
    recover_fingerprint_db_only,
)
from mediatracker.hive.share_policy import grant_share_intent

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_fingerprint_daemon_session():
    from mediatracker.hive import fingerprint_daemon as daemon_mod

    daemon_mod.clear_active_session()
    yield
    daemon_mod.clear_active_session()


def test_tick_recover_db_only_never_calls_digest_prefix(client, app, settings, monkeypatch):
    """Recover du tick : pas de relecture disque sur toute la file d'empreintes partielles."""
    from mediatracker.hive import fingerprint as fp_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        for tmdb_id in (89410, 89411, 89412):
            _, item = _film_with_file(db, app, settings, tmdb_id=tmdb_id, write_hash=False)
            path = Path(item.file_path or "")
            size = len(path.read_bytes())
            db.merge(
                HiveFileFingerprint(
                    media_item_id=item.id,
                    path=str(path),
                    size=size,
                    mtime_ns=int(path.stat().st_mtime_ns),
                    partial_size=size // 2,
                    partial_sha256_hex="a" * 64,
                    updated_at=NOW,
                )
            )
        db.commit()
    finally:
        db.close()

    def fail_digest(*_args, **_kwargs):
        raise AssertionError("_digest_prefix ne doit pas être appelé pendant recover tick")

    monkeypatch.setattr(fp_mod, "_digest_prefix", fail_digest)
    tick_db = app.state.session_factory()
    try:
        recover_fingerprint_db_only(tick_db, now=NOW)
        tick_db.commit()
        hive_fingerprint_tick(tick_db, settings, now=NOW)
        tick_db.commit()
    finally:
        tick_db.close()


def test_recover_clears_stale_claim_when_sqlite_returns_naive_utc(client, app, settings):
    """SQLite renvoie des datetimes naïfs : ne pas lever TypeError vs now aware."""
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=89400, write_hash=False)
        stale_claim = (NOW - HASH_CLAIM_STALE - timedelta(minutes=1)).replace(tzinfo=None)
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=item.file_path or "",
                size=1024,
                mtime_ns=1,
                partial_size=0,
                updated_at=NOW,
                hash_claimed_at=stale_claim,
            )
        )
        db.commit()
        recover_fingerprint_db_only(db, now=NOW)
        db.commit()
        row = db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert row.hash_claimed_at is None
    finally:
        db.close()


def test_recover_resets_partial_at_size_without_sha(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=89401, write_hash=False)
        fp = HiveFileFingerprint(
            media_item_id=item.id,
            path=item.file_path or "",
            size=1024,
            mtime_ns=1,
            partial_size=1024,
            updated_at=NOW - timedelta(hours=1),
            hash_claimed_at=NOW - HASH_CLAIM_STALE - timedelta(minutes=1),
        )
        db.add(fp)
        db.commit()
        recover_fingerprint_db_only(db, now=NOW)
        db.commit()
        row = db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert int(row.partial_size or 0) == 0
        assert row.hash_claimed_at is None
    finally:
        db.close()


def test_fingerprint_partial_sha_persisted_and_monotone(client, app, settings, monkeypatch):
    from mediatracker.hive import fingerprint as fp_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 2)
        _, item = _film_with_file(db, app, settings, tmdb_id=89402, write_hash=False)
        path = Path(item.file_path or "")
        payload = path.read_bytes()
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        row = db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert row.partial_sha256_hex
        prefix = hashlib.sha256(payload[: int(row.partial_size or 0)]).hexdigest()
        assert row.partial_sha256_hex == prefix
        assert row.partial_sha256_hex
        if row.sha256_hex:
            assert row.partial_sha256_hex == row.sha256_hex
        else:
            assert int(row.partial_size or 0) < int(row.size or 0)
    finally:
        db.close()


def test_ensure_fingerprint_wakes_worker(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        reset_wake_metrics(db)
        db.commit()
        _, item = _film_with_file(db, app, settings, tmdb_id=89403, write_hash=False)
        grant_share_intent(db, item.id)
        pending = ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        assert pending
        counts = wake_counts(db)
        assert counts.get(REASON_HIVE_FINGERPRINT, 0) >= 1
    finally:
        db.close()


def test_fingerprint_journal_dedupe_on_complete(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=89404, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        while True:
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            row = db.get(HiveFileFingerprint, item.id)
            assert row is not None
            if row.sha256_hex:
                break
        hive_fingerprint_tick(db, settings, now=NOW + timedelta(seconds=1))
        db.commit()
        n_done = db.scalar(
            select(func.count()).select_from(Event).where(Event.type == "hive.fingerprint.done")
        )
        assert int(n_done or 0) == 1
    finally:
        db.close()


def test_share_api_progress_capped_below_100_while_preparing(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=89405, write_hash=False)
        path = Path(item.file_path or "")
        size = len(path.read_bytes())
        grant_share_intent(db, item.id)
        fp = HiveFileFingerprint(
            media_item_id=item.id,
            path=str(path),
            size=size,
            mtime_ns=int(path.stat().st_mtime_ns),
            partial_size=size,
            updated_at=NOW,
        )
        db.merge(fp)
        db.commit()
        res = client.get(f"/api/v1/hive/items/{item.id}/sharing")
        assert res.status_code == 200
        body = res.json()
        assert body["preparing"] is True
        assert body["fingerprint_partial_size"] == size - 1
    finally:
        db.close()

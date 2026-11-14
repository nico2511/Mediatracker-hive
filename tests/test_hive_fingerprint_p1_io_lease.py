"""P1 relecture : I/O transitoire en session active et lease file running."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.test_hive_catalog_sharing_worker import _enable_hive
from tests.test_hive_fingerprint_daemon_concurrency import _film_indexed_on_disk

from mediatracker.db.models import HiveFileFingerprint, HiveFingerprintQueue
from mediatracker.hive import fingerprint as fp_mod
from mediatracker.hive import fingerprint_daemon as daemon_mod
from mediatracker.hive import fingerprint_stream as fstream_mod
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.fingerprint_queue import recover_fingerprint_queue
from mediatracker.hive.share_policy import grant_share_intent

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 10, 22, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _reset_fingerprint_daemon_state():
    daemon_mod.clear_active_session()
    daemon_mod._transient_claim_errors.clear()
    yield
    daemon_mod.clear_active_session()
    daemon_mod._transient_claim_errors.clear()
STALE_CLAIM = NOW - timedelta(minutes=25)


def test_continue_active_transient_stat_recovers_without_invalidate(client, app, settings, monkeypatch):
    """EIO sur stat en session : retry borné comme au démarrage, pas d'invalidation."""
    import mediatracker.hive.fingerprint_daemon as daemon_stat

    _enable_hive(client)
    monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 4)
    file_len = 512 * 1024
    db = app.state.session_factory()
    try:
        item = _film_indexed_on_disk(
            db, app, settings, tmdb_id=88200, file_len=file_len, stem="io-stat"
        )
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    real_stat = fp_mod._stat_fingerprint
    continue_calls = {"n": 0}

    def flaky_stat(path):
        if daemon_mod._active_item_id is not None:
            continue_calls["n"] += 1
            if continue_calls["n"] <= 2:
                raise OSError("EIO simulé pendant session")
        return real_stat(path)

    monkeypatch.setattr(fp_mod, "_stat_fingerprint", flaky_stat)
    monkeypatch.setattr(daemon_stat, "_stat_fingerprint", flaky_stat)

    tick_db = app.state.session_factory()
    try:
        for _ in range(12):
            hive_fingerprint_tick(tick_db, settings, now=NOW)
            tick_db.commit()
            row = tick_db.get(HiveFileFingerprint, item.id)
            if row is not None and int(row.partial_size or 0) > 0:
                break
        row = tick_db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert int(row.partial_size or 0) > 0
        assert not (row.hash_error or "").strip()
        q = tick_db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        if q is not None:
            assert q.status != "failed"
    finally:
        tick_db.close()


def test_continue_active_transient_stat_exhaustion_frees_neighbor(client, app, settings, monkeypatch):
    """Après 5 EIO en session sur A, failed puis voisin B avance."""
    _enable_hive(client)
    _patch_small_read_budget(monkeypatch)
    db = app.state.session_factory()
    try:
        item_a = _film_indexed_on_disk(db, app, settings, tmdb_id=88201, file_len=8 * 1024 * 1024, stem="io-ex-a")
        item_b = _film_indexed_on_disk(db, app, settings, tmdb_id=88202, file_len=256 * 1024, stem="io-ex-b")
        for item in (item_a, item_b):
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    real_classify = fp_mod._classify_disk_against_snapshot

    def fail_active_item_a(path, snapshot):
        if snapshot.media_item_id == item_a.id and daemon_mod._active_item_id == item_a.id:
            return "io_error"
        return real_classify(path, snapshot)

    monkeypatch.setattr(fp_mod, "_classify_disk_against_snapshot", fail_active_item_a)
    monkeypatch.setattr(daemon_mod, "_classify_disk_against_snapshot", fail_active_item_a)

    tick_db = app.state.session_factory()
    try:
        for _ in range(30):
            hive_fingerprint_tick(tick_db, settings, now=NOW)
            tick_db.commit()
            q_a = tick_db.scalar(
                select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item_a.id)
            )
            fp_b = tick_db.get(HiveFileFingerprint, item_b.id)
            if q_a is not None and q_a.status == "failed":
                if fp_b is not None and int(fp_b.partial_size or 0) > 0:
                    break
        q_a = tick_db.scalar(
            select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item_a.id)
        )
        assert q_a is not None and q_a.status == "failed"
        fp_b = tick_db.get(HiveFileFingerprint, item_b.id)
        assert fp_b is not None
        assert int(fp_b.partial_size or 0) > 0 or (fp_b.sha256_hex or "").strip()
    finally:
        tick_db.close()


def _patch_small_read_budget(monkeypatch, *, chunks: int = 2):
    budget = chunks * fp_mod.CHUNK_SIZE
    monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", chunks)
    monkeypatch.setattr(fstream_mod, "MAX_CHUNKS_PER_TICK", chunks)
    monkeypatch.setattr(fstream_mod, "READ_BUDGET_BYTES", budget)
    monkeypatch.setattr(daemon_mod, "READ_BUDGET_BYTES", budget)


def test_continue_active_read_budget_io_transient(client, app, settings, monkeypatch):
    """EIO sans octets lus : pending, reprise sans SHA ni invalidation."""
    _enable_hive(client)
    _patch_small_read_budget(monkeypatch, chunks=1)
    file_len = 2 * 1024 * 1024
    db = app.state.session_factory()
    try:
        item = _film_indexed_on_disk(
            db, app, settings, tmdb_id=88203, file_len=file_len, stem="io-read"
        )
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    orig_open = Path.open
    opens = {"fail_once": True}

    def flaky_open(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        session = daemon_mod.active_session_for_tests()
        if (
            "b" in str(mode)
            and daemon_mod._active_item_id is not None
            and session is not None
            and session.offset > 0
            and opens["fail_once"]
        ):
            opens["fail_once"] = False
            raise OSError("EIO lecture")
        return orig_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", flaky_open)

    tick_db = app.state.session_factory()
    try:
        for _ in range(6):
            hive_fingerprint_tick(tick_db, settings, now=NOW)
            tick_db.commit()
        row = tick_db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert not (row.hash_error or "").strip()
        assert opens["fail_once"] is False
        assert int(row.partial_size or 0) > 0
    finally:
        tick_db.close()


def test_live_session_keeps_running_claim_past_stale_window(client, app, settings, monkeypatch):
    """Session RAM active : recover ne repasse pas running>périmètre en pending."""
    _enable_hive(client)
    _patch_small_read_budget(monkeypatch, chunks=1)
    db = app.state.session_factory()
    try:
        item = _film_indexed_on_disk(
            db, app, settings, tmdb_id=88210, file_len=4 * 1024 * 1024, stem="lease-live"
        )
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        assert daemon_mod._active_item_id == item.id
        q_row = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q_row is not None and q_row.status == "running"
        q_row.claimed_at = STALE_CLAIM.replace(tzinfo=None)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        q_row = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q_row is not None
        assert q_row.status == "running"
        assert daemon_mod._active_item_id == item.id
        claimed = q_row.claimed_at
        assert claimed is not None
        if claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
        assert claimed >= NOW - timedelta(seconds=5)
    finally:
        db.close()


def test_crash_recovery_requeues_stale_running_without_session(client, app, settings):
    """Sans session : claim running périmé revient pending (crash)."""
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        item = _film_indexed_on_disk(
            db, app, settings, tmdb_id=88211, file_len=64 * 1024, stem="lease-crash"
        )
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        q_row = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q_row is not None
        q_row.status = "running"
        q_row.claimed_at = STALE_CLAIM.replace(tzinfo=None)
        db.commit()
        daemon_mod.clear_active_session()
        recover_fingerprint_queue(db, now=NOW)
        db.commit()
        q_row = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q_row is not None
        assert q_row.status == "pending"
        assert q_row.claimed_at is None
    finally:
        db.close()

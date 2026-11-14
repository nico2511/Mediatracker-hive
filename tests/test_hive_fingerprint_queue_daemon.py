"""File d'attente et processus dédié empreintes Hive."""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from tests.test_hive_catalog_sharing_worker import _enable_hive
from tests.test_hive_sharing_catalog_entry_local import _film_with_file

from mediatracker.db.models import HiveFileFingerprint, HiveFingerprintQueue
from mediatracker.hive import fingerprint_daemon as daemon_mod
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.fingerprint_queue import (
    backfill_queue_from_incomplete,
    count_pending_ordered,
    enqueue_fingerprint_work,
    recover_fingerprint_queue,
)
from mediatracker.hive.fingerprint_stream import READ_BUDGET_BYTES, StreamingSha256Session
from mediatracker.hive.share_policy import grant_share_intent

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
_MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _reset_fingerprint_daemon_session():
    daemon_mod.clear_active_session()
    yield
    daemon_mod.clear_active_session()


def test_worker_pass_does_not_run_fingerprint_tick():
    import inspect

    import mediatracker.worker as worker_mod

    src = inspect.getsource(worker_mod._run_worker_pass)
    assert "hive_fingerprint_tick" not in src
    assert "empreintes" not in src.lower() or "Hive catalogue" in src


def test_slow_source_poll_does_not_block_fingerprint_daemon(client, app, settings, monkeypatch):
    import mediatracker.worker as worker_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97101, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    def slow_poll(*_a, **_k):
        time.sleep(0.4)

    monkeypatch.setattr(worker_mod, "worker_poll_if_due", slow_poll)
    monkeypatch.setattr(worker_mod, "pump_forced_source_check_worker", lambda *_a, **_k: False)

    tick_db = app.state.session_factory()
    try:
        hive_fingerprint_tick(tick_db, settings, now=NOW)
        tick_db.commit()
        row = tick_db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert int(row.partial_size or 0) > 0
    finally:
        tick_db.close()

    worker_db = app.state.session_factory()
    try:
        worker_mod._poll_sources_isolated(app.state.session_factory, settings)
    finally:
        worker_db.close()


def test_queue_fifo_order_and_dedup_on_reenqueue(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        for tmdb_id in (97110, 97111, 97112):
            _, item = _film_with_file(db, app, settings, tmdb_id=tmdb_id, write_hash=False)
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.flush()
        order_first = count_pending_ordered(db)
        assert len(order_first) == 3
        enqueue_fingerprint_work(db, order_first[0], now=NOW + timedelta(seconds=1))
        db.flush()
        order_second = count_pending_ordered(db)
        assert order_second == order_first
    finally:
        db.close()


def test_single_consumer_one_running_at_a_time(client, app, settings):
    from sqlalchemy import func, select

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        for tmdb_id in (97122, 97123):
            _, item = _film_with_file(db, app, settings, tmdb_id=tmdb_id, write_hash=False)
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        n_running = db.scalar(
            select(func.count())
            .select_from(HiveFingerprintQueue)
            .where(HiveFingerprintQueue.status == "running")
        )
        assert int(n_running or 0) <= 1
        assert daemon_mod.active_session_for_tests() is not None
    finally:
        db.close()


def test_restart_rebuilds_prefix_once_not_each_budget(tmp_path, monkeypatch):
    size = 40 * _MIB
    path = tmp_path / "resume-fictive.bin"
    path.write_bytes(bytes((n * 13) % 256 for n in range(size)))
    prefix_len = 10 * _MIB
    expected = hashlib.sha256(path.read_bytes()[:prefix_len]).hexdigest()

    resume_calls = 0
    original = StreamingSha256Session.resume_from_disk

    def counting_resume(*args, **kwargs):
        nonlocal resume_calls
        resume_calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(StreamingSha256Session, "resume_from_disk", staticmethod(counting_resume))
    session = StreamingSha256Session.resume_from_disk(
        path, size=size, partial=prefix_len, expected_prefix_hex=expected
    )
    assert session is not None
    assert resume_calls == 1
    before = resume_calls
    session.read_budget(READ_BUDGET_BYTES)
    session.read_budget(READ_BUDGET_BYTES)
    assert resume_calls == before


def test_streaming_budget_avoids_re_reading_prefix_between_ticks(tmp_path):
    size = 40 * _MIB
    path = tmp_path / "stream-fictive.bin"
    path.write_bytes(b"\x07" * size)
    session = StreamingSha256Session.open_fresh(path, size=size)
    session.read_budget(READ_BUDGET_BYTES)
    assert session.bytes_read_last_budget == READ_BUDGET_BYTES
    assert session.offset == READ_BUDGET_BYTES
    session.read_budget(READ_BUDGET_BYTES)
    assert session.bytes_read_last_budget == size - READ_BUDGET_BYTES


def test_crash_recovery_requeues_and_reclaims(client, app, settings):
    from sqlalchemy import select

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97130, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        q_row = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q_row is not None
        q_row.status = "running"
        q_row.claimed_at = (NOW - timedelta(hours=1)).replace(tzinfo=None)
        db.commit()
        recover_fingerprint_queue(db, now=NOW)
        db.commit()
        q_row = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q_row is not None
        assert q_row.status == "pending"
        assert q_row.claimed_at is None
    finally:
        db.close()


def test_permanent_failure_does_not_block_neighbor(client, app, settings):
    from mediatracker.hive.fingerprint_queue import mark_queue_failed

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item_bad = _film_with_file(db, app, settings, tmdb_id=97140, write_hash=False)
        _, item_ok = _film_with_file(db, app, settings, tmdb_id=97141, write_hash=False)
        for item in (item_bad, item_ok):
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        daemon_mod.clear_active_session()
        mark_queue_failed(db, item_bad.id, reason="Erreur permanente simulée.", now=NOW)
        bad_fp = db.get(HiveFileFingerprint, item_bad.id)
        assert bad_fp is not None
        bad_fp.hash_error = "Erreur permanente simulée."
        db.commit()
        for _ in range(40):
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            ok_fp = db.get(HiveFileFingerprint, item_ok.id)
            if ok_fp is not None and ok_fp.sha256_hex:
                break
        else:
            raise AssertionError("le voisin en file n'a pas terminé malgré l'échec permanent du premier")
        assert ok_fp is not None and ok_fp.sha256_hex
    finally:
        db.close()


def test_fingerprint_progress_commits_outside_worker_catalog_rollback(client, app, settings, monkeypatch):
    import mediatracker.worker as worker_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97150, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        partial_before = int(db.get(HiveFileFingerprint, item.id).partial_size or 0)
        assert partial_before > 0
    finally:
        db.close()

    def _boom_catalogue(*_a, **_k):
        raise RuntimeError("catalogue")

    monkeypatch.setattr(worker_mod, "sync_hive_catalog", _boom_catalogue)
    session = app.state.session_factory()
    try:
        worker_mod._run_worker_pass(session, settings, app.state.session_factory)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()

    check = app.state.session_factory()
    try:
        fp = check.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert int(fp.partial_size or 0) == partial_before
    finally:
        check.close()


def test_sha_and_catalog_only_after_complete(client, app, settings, monkeypatch):
    from mediatracker.hive import fingerprint as fp_mod

    _enable_hive(client)
    sync_calls: list[int] = []
    monkeypatch.setattr(
        daemon_mod,
        "sync_hive_catalog_item",
        lambda *_a, **_k: sync_calls.append(1),
    )

    db = app.state.session_factory()
    try:
        monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 2)
        _, item = _film_with_file(db, app, settings, tmdb_id=97160, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        for _ in range(50):
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            row = db.get(HiveFileFingerprint, item.id)
            assert row is not None
            if not row.sha256_hex:
                assert sync_calls == []
            else:
                break
        assert row is not None and row.sha256_hex
        assert len(sync_calls) == 1
    finally:
        db.close()


def test_backfill_after_crash(client, app, settings):
    from sqlalchemy import func, select

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97170, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.execute(HiveFingerprintQueue.__table__.delete())
        db.commit()
        assert backfill_queue_from_incomplete(db, now=NOW) >= 1
        db.commit()
        n = db.scalar(
            select(func.count())
            .select_from(HiveFingerprintQueue)
            .where(HiveFingerprintQueue.media_item_id == item.id)
        )
        assert int(n or 0) == 1
    finally:
        db.close()


def _set_hive_enabled(db, *, enabled: bool) -> None:
    from mediatracker.db.models import Setting

    db.merge(
        Setting(
            key="hive_enabled",
            value="true" if enabled else "false",
            is_secret=False,
            updated_at=NOW,
        )
    )
    db.flush()


def test_revoke_share_mid_hash_releases_session_and_neighbor_ticks(client, app, settings):
    from sqlalchemy import select

    from mediatracker.hive.share_policy import revoke_share_intent

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item_a = _film_with_file(db, app, settings, tmdb_id=97200, write_hash=False)
        _, item_b = _film_with_file(db, app, settings, tmdb_id=97201, write_hash=False)
        for item in (item_a, item_b):
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        assert daemon_mod.active_session_for_tests() is not None
        assert daemon_mod._active_item_id == item_a.id
        revoke_share_intent(db, item_a.id)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        q_a = db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item_a.id))
        assert q_a is None
        assert db.get(HiveFileFingerprint, item_a.id) is None
        fp_b = db.get(HiveFileFingerprint, item_b.id)
        assert fp_b is not None
        assert int(fp_b.partial_size or 0) > 0 or bool(fp_b.sha256_hex)
    finally:
        db.close()


def test_hive_disabled_skips_fingerprint_disk_io(client, app, settings, monkeypatch):
    from mediatracker.hive import fingerprint as fp_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97210, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    disk_calls: list[int] = []

    def forbid_stat(*_a, **_k):
        disk_calls.append(1)
        raise AssertionError("I/O empreinte interdite quand Hive est désactivé")

    monkeypatch.setattr(fp_mod, "_stat_fingerprint", forbid_stat)
    def forbid_read(*_a, **_k):
        raise AssertionError("read interdit")

    monkeypatch.setattr(StreamingSha256Session, "read_budget", forbid_read)

    tick_db = app.state.session_factory()
    try:
        _set_hive_enabled(tick_db, enabled=False)
        tick_db.commit()
        hive_fingerprint_tick(tick_db, settings, now=NOW)
        tick_db.commit()
        assert disk_calls == []
        assert daemon_mod.active_session_for_tests() is None
    finally:
        tick_db.close()


def test_hive_disabled_then_enabled_resumes_work(client, app, settings):
    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97211, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        _set_hive_enabled(db, enabled=False)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert int(fp.partial_size or 0) == 0
        _set_hive_enabled(db, enabled=True)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert int(fp.partial_size or 0) > 0
    finally:
        db.close()


def test_concurrent_db_mtime_reset_prevents_stale_sha(client, app, settings, monkeypatch):
    from mediatracker.hive import fingerprint as fp_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 2)
        _, item = _film_with_file(db, app, settings, tmdb_id=97220, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert int(fp.partial_size or 0) > 0
        Path(item.file_path or "").write_bytes(b"tampered")
        hive_fingerprint_tick(db, settings, now=NOW)
        db.commit()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert (fp.sha256_hex or "").strip() == ""
        assert (fp.hash_error or "").strip() != ""
    finally:
        db.close()


def test_transient_stat_errors_then_success(client, app, settings, monkeypatch):
    import mediatracker.hive.fingerprint_daemon as daemon_mod_stat
    from mediatracker.hive import fingerprint as fp_mod

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item = _film_with_file(db, app, settings, tmdb_id=97212, write_hash=False)
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    real_stat = fp_mod._stat_fingerprint
    calls = {"n": 0}

    def flaky_stat(path):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("simulé EIO")
        return real_stat(path)

    monkeypatch.setattr(daemon_mod_stat, "_stat_fingerprint", flaky_stat)

    tick_db = app.state.session_factory()
    try:
        for _ in range(6):
            hive_fingerprint_tick(tick_db, settings, now=NOW)
            tick_db.commit()
            row = tick_db.get(HiveFileFingerprint, item.id)
            if row is not None and int(row.partial_size or 0) > 0:
                break
        row = tick_db.get(HiveFileFingerprint, item.id)
        assert row is not None
        assert int(row.partial_size or 0) > 0
        from sqlalchemy import select

        q = tick_db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id))
        assert q is not None
        assert q.status != "failed"
    finally:
        tick_db.close()

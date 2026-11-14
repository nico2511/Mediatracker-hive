"""Concurrence empreintes : barrières déterministes sur le daemon streaming."""

from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from tests.test_hive_catalog_sharing_worker import _enable_hive
from tests.test_hive_sharing_catalog_entry_local import _film_with_file

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import HiveFileFingerprint, HiveFingerprintQueue, LibraryFile, Media, MediaItem
from mediatracker.hive import fingerprint as fp_mod
from mediatracker.hive import fingerprint_daemon as daemon_mod
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
from mediatracker.hive.fingerprint_stream import READ_BUDGET_BYTES, StreamingSha256Session
from mediatracker.hive.share_policy import grant_share_intent, revoke_share_intent

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 10, 14, 0, tzinfo=timezone.utc)
CONCURRENT_MTIME_NS = 9_001_002_003_004_005


@pytest.fixture(autouse=True)
def _reset_fingerprint_daemon_session():
    daemon_mod.clear_active_session()
    yield
    daemon_mod.clear_active_session()


def _film_indexed_on_disk(
    db, app, settings, *, tmdb_id: int, file_len: int, stem: str
) -> MediaItem:
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / f"{stem}.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x" * file_len)
    media = Media(type="film", title=stem, year=2026, tmdb_id=tmdb_id)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=str(video),
            size=file_len,
            parsed_title=stem,
            parsed_year=2026,
            media_type="film",
            tmdb_id=tmdb_id,
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
    )
    db.flush()
    return item


def _pause_after_first_read_budget(monkeypatch, gate: threading.Event, proceed: threading.Event):
    orig = StreamingSha256Session.read_budget
    state = {"paused": False}

    def pausing(self: StreamingSha256Session, budget: int | None = None):
        if not state["paused"] and self.offset < self.size:
            state["paused"] = True
            chunk_cap = min(65536, budget if budget is not None else READ_BUDGET_BYTES)
            offset_before = self.offset
            got = orig(self, chunk_cap)
            if self.offset > offset_before:
                gate.set()
                assert proceed.wait(timeout=10)
            return got
        return orig(self, budget)

    monkeypatch.setattr(StreamingSha256Session, "read_budget", pausing)


def test_daemon_barrier_db_mtime_mutation_blocks_stale_sha(client, app, settings, monkeypatch):
    """Lecture terminée en mémoire : mutation DB entre validation et UPDATE → pas de SHA."""
    _enable_hive(client)
    file_len = 128 * 1024
    monkeypatch.setattr(fp_mod, "CHUNK_SIZE", 65536)
    gate = threading.Event()
    proceed = threading.Event()

    def barrier():
        gate.set()
        proceed.wait(timeout=10)

    monkeypatch.setattr(fp_mod, "_apply_pre_write_barrier", barrier)

    db = app.state.session_factory()
    try:
        item = _film_indexed_on_disk(
            db, app, settings, tmdb_id=88089, file_len=file_len, stem="daemon-barrier"
        )
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        item_id = item.id
        db.commit()
    finally:
        db.close()

    finalized = {"v": False}
    orig_apply = fp_mod._apply_hash_result

    def track_apply(db, snapshot, *, read_upto, digest, moment, chunk_digests=None):
        # Même signature que fingerprint._apply_hash_result (PR247 : chunk_digests), relayée telle quelle.
        if read_upto >= snapshot.size:
            finalized["v"] = True
        return orig_apply(
            db, snapshot, read_upto=read_upto, digest=digest, moment=moment, chunk_digests=chunk_digests
        )

    monkeypatch.setattr(fp_mod, "_apply_hash_result", track_apply)
    monkeypatch.setattr(daemon_mod, "_apply_hash_result", track_apply)

    sync_calls: list[int] = []
    monkeypatch.setattr(daemon_mod, "sync_hive_catalog_item", lambda *_a, **_k: sync_calls.append(1))

    def runner():
        session = app.state.session_factory()
        try:
            for _ in range(20):
                hive_fingerprint_tick(session, settings, now=NOW)
                session.commit()
                if gate.is_set():
                    break
        finally:
            session.close()

    thread = threading.Thread(target=runner, name="daemon-barrier")
    thread.start()
    assert gate.wait(timeout=10)
    other = app.state.session_factory()
    try:
        fp = other.get(HiveFileFingerprint, item_id)
        assert fp is not None
        fp.mtime_ns = CONCURRENT_MTIME_NS
        fp.partial_size = 0
        fp.sha256_hex = None
        other.commit()
    finally:
        other.close()
    proceed.set()
    thread.join(timeout=30)

    check = app.state.session_factory()
    try:
        fp = check.get(HiveFileFingerprint, item_id)
        assert finalized["v"]
        assert fp is not None
        assert fp.sha256_hex is None
        assert int(fp.mtime_ns or 0) == CONCURRENT_MTIME_NS
        assert sync_calls == []
    finally:
        check.close()


def test_daemon_read_pause_db_reset_rejects_final_sha(client, app, settings, monkeypatch):
    """Pause après 1er budget lecture ; reset DB ; reprise → pas de SHA final périmé."""
    _enable_hive(client)
    file_len = 256 * 1024
    monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 4)
    gate = threading.Event()
    proceed = threading.Event()
    _pause_after_first_read_budget(monkeypatch, gate, proceed)

    db = app.state.session_factory()
    try:
        item = _film_indexed_on_disk(
            db, app, settings, tmdb_id=88090, file_len=file_len, stem="daemon-stale-read"
        )
        grant_share_intent(db, item.id)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        item_id = item.id
        db.commit()
    finally:
        db.close()

    wrong_hex = hashlib.sha256(b"x" * file_len).hexdigest()

    def runner():
        session = app.state.session_factory()
        try:
            for _ in range(30):
                hive_fingerprint_tick(session, settings, now=NOW)
                session.commit()
                fp = session.get(HiveFileFingerprint, item_id)
                if fp is not None and fp.sha256_hex:
                    break
                if gate.is_set() and not proceed.is_set():
                    break
        finally:
            session.close()

    thread = threading.Thread(target=runner)
    thread.start()
    assert gate.wait(timeout=10)
    other = app.state.session_factory()
    try:
        fp = other.get(HiveFileFingerprint, item_id)
        assert fp is not None
        fp.partial_size = 0
        fp.partial_sha256_hex = None
        fp.sha256_hex = None
        fp.mtime_ns = CONCURRENT_MTIME_NS
        other.commit()
    finally:
        other.close()
    proceed.set()
    thread.join(timeout=30)

    check = app.state.session_factory()
    try:
        fp = check.get(HiveFileFingerprint, item_id)
        assert fp is not None
        assert fp.sha256_hex is None
        assert fp.sha256_hex != wrong_hex
        assert int(fp.mtime_ns or 0) == CONCURRENT_MTIME_NS
    finally:
        check.close()


def test_revoke_share_during_read_pause_neighbor_advances(client, app, settings, monkeypatch):
    monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 2)
    gate = threading.Event()
    proceed = threading.Event()
    _pause_after_first_read_budget(monkeypatch, gate, proceed)

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item_a = _film_with_file(db, app, settings, tmdb_id=88100, write_hash=False)
        _, item_b = _film_with_file(db, app, settings, tmdb_id=88101, write_hash=False)
        for item in (item_a, item_b):
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    sync_calls: list[int] = []
    monkeypatch.setattr(daemon_mod, "sync_hive_catalog_item", lambda *_a, **_k: sync_calls.append(1))

    def runner():
        session = app.state.session_factory()
        try:
            hive_fingerprint_tick(session, settings, now=NOW)
            session.commit()
            proceed.set()
            for _ in range(15):
                hive_fingerprint_tick(session, settings, now=NOW)
                session.commit()
                fp_b = session.get(HiveFileFingerprint, item_b.id)
                if fp_b is not None and int(fp_b.partial_size or 0) > 0:
                    break
        finally:
            session.close()

    thread = threading.Thread(target=runner)
    thread.start()
    assert gate.wait(timeout=10)
    inner = app.state.session_factory()
    try:
        revoke_share_intent(inner, item_a.id)
        inner.commit()
    finally:
        inner.close()
    proceed.set()
    thread.join(timeout=60)

    check = app.state.session_factory()
    try:
        assert check.get(HiveFileFingerprint, item_a.id) is None
        assert (
            check.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item_a.id))
            is None
        )
        fp_b = check.get(HiveFileFingerprint, item_b.id)
        assert fp_b is not None
        assert int(fp_b.partial_size or 0) > 0
        assert sync_calls == []
        assert daemon_mod.active_session_for_tests() is None or daemon_mod._active_item_id == item_b.id
    finally:
        check.close()


def test_reenqueue_second_item_while_first_paused(client, app, settings, monkeypatch):
    """Session abandonnée en pause : claim libéré, voisin B avance sans SHA sur A."""
    from mediatracker.hive.fingerprint_queue import mark_queue_failed, release_running_to_pending

    monkeypatch.setattr(fp_mod, "MAX_CHUNKS_PER_TICK", 2)
    gate = threading.Event()
    proceed = threading.Event()
    _pause_after_first_read_budget(monkeypatch, gate, proceed)

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _, item_a = _film_with_file(db, app, settings, tmdb_id=88110, write_hash=False)
        _, item_b = _film_with_file(db, app, settings, tmdb_id=88111, write_hash=False)
        for item in (item_a, item_b):
            grant_share_intent(db, item.id)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        db.commit()
    finally:
        db.close()

    def start_first():
        session = app.state.session_factory()
        try:
            hive_fingerprint_tick(session, settings, now=NOW)
            session.commit()
        finally:
            session.close()

    thread = threading.Thread(target=start_first)
    thread.start()
    assert gate.wait(timeout=10)
    assert daemon_mod._active_item_id == item_a.id
    proceed.set()
    thread.join(timeout=30)

    db = app.state.session_factory()
    try:
        release_running_to_pending(db, item_a.id, now=NOW)
        mark_queue_failed(db, item_a.id, reason="session abandonnée (test)", now=NOW)
        daemon_mod.clear_active_session()
        db.commit()
        fp_b = None
        for _ in range(25):
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            fp_b = db.get(HiveFileFingerprint, item_b.id)
            if fp_b is not None and int(fp_b.partial_size or 0) > 0:
                break
        fp_a = db.get(HiveFileFingerprint, item_a.id)
        assert fp_a is not None and not (fp_a.sha256_hex or "").strip()
        assert fp_b is not None and int(fp_b.partial_size or 0) > 0
    finally:
        db.close()

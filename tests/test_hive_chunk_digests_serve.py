"""Empreintes morceaux persistées : GET /hive/files sans lecture intégrale."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.hive_chunk_digest_testutil import (
    persist_chunk_digests_corrupt,
    persist_chunk_digests_for_test_file,
    run_chunk_backfill_until_ready,
)
from tests.test_hive_t3_peer_http import _enable, _settings_pair
from tests.test_hive_t5 import NOW, URL_A, _join_ruche, _seed_file_on_a

from mediatracker.api.app import create_app
from mediatracker.db.models import HiveFileFingerprint, MediaItem, Setting
from mediatracker.hive import fingerprint_daemon as fingerprint_daemon_mod
from mediatracker.hive.chunk_digest import TRANSFER_CHUNK_SIZE, chunk_digests_for_file
from mediatracker.hive.chunk_digest_backfill import (
    chunk_digest_backfill_tick,
    clear_active_chunk_session,
)
from mediatracker.hive.chunk_digest_store import (
    HIVE_CHUNK_DIGESTS_REQUIRED,
    chunk_digests_row_valid,
    count_pending_chunk_backfill,
    fingerprint_needs_chunk_backfill,
    list_backfill_candidate_ids,
)
from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item
from mediatracker.hive.fingerprint_daemon import fingerprint_daemon_tick
from mediatracker.hive.fingerprint_queue import enqueue_fingerprint_work
from mediatracker.hive.identity import hive_enabled_in_db, load_private_key
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.share_policy import grant_share_intent

_skip_legacy_chunk_backfill = pytest.mark.skipif(
    not HIVE_CHUNK_DIGESTS_REQUIRED,
    reason="Backfill morceaux désactivé (fetch-grant / LOT4+).",
)


@pytest.fixture(autouse=True)
def _reset_chunk_backfill_session():
    clear_active_chunk_session()
    fingerprint_daemon_mod.clear_active_session()
    yield
    clear_active_chunk_session()
    fingerprint_daemon_mod.clear_active_session()


@pytest.fixture
def pair_apps(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "cds_a"))
    app_b = create_app(_settings_pair(tmp_path, "cds_b"))
    for app in (app_a, app_b):
        app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()


def _fp_for_payload(app, payload: bytes) -> HiveFileFingerprint:
    db = app.state.session_factory()
    try:
        return db.scalar(
            select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)).limit(1)
        )
    finally:
        db.close()


def _prepare_digests_db(app, fp: HiveFileFingerprint) -> None:
    db = app.state.session_factory()
    try:
        row = db.get(HiveFileFingerprint, fp.media_item_id)
        assert row is not None
        persist_chunk_digests_for_test_file(db, row)
        db.commit()
    finally:
        db.close()


def _signed_get(client: TestClient, app, rid: bytes, fid_hex: str, *, start: int = 0, end: int = 99):
    path = f"/hive/files/{fid_hex}"
    pk = load_private_key(app.state.settings)
    headers = sign_request_headers(
        private_key=pk, method="GET", path=path, query="", ruche_id=rid, body=b""
    )
    headers["Range"] = f"bytes={start}-{end}"
    return client.get(path, headers=headers)


def _forbid_full_file_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        raise AssertionError("chunk_digests_for_file must not run on GET /hive/files path")

    monkeypatch.setattr("mediatracker.hive.chunk_digest.chunk_digests_for_file", _boom)


def test_get_never_calls_full_digest_when_digests_in_db(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = os.urandom(64 * 1024)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        fp = _fp_for_payload(app_a, payload)
        _prepare_digests_db(app_a, fp)
        _forbid_full_file_digest(monkeypatch)
        resp = _signed_get(ca, app_a, rid, fid, end=99)
        assert resp.status_code == 206, resp.status_code
        assert "Hive-Chunk-Sha256" not in resp.headers


def test_legacy_without_digests_serves_without_full_read(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = b"x" * 4096
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)))
            from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

            clear_chunk_digest_state(fp)
            db.commit()
        finally:
            db.close()
        _forbid_full_file_digest(monkeypatch)
        resp = _signed_get(ca, app_a, rid, fid)
        assert resp.status_code in (200, 206), resp.text
        assert "Hive-Chunk-Sha256" not in resp.headers


def test_backfill_then_get_slow_disk(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = os.urandom(8 * 1024 * 1024)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, sha = _seed_file_on_a(app_a, rid, payload)
        fp = _fp_for_payload(app_a, payload)
        video = Path(fp.path)
        orig_open = Path.open
        delay = 0.01

        def slow_open(self, mode="r", *args, **kwargs):
            handle = orig_open(self, mode, *args, **kwargs)
            if self.resolve() != video.resolve() or "b" not in mode:
                return handle
            orig_read = handle.read

            def delayed_read(size=-1):
                time.sleep(delay)
                return orig_read(size)

            handle.read = delayed_read  # type: ignore[method-assign]
            return handle

        _forbid_full_file_digest(monkeypatch)
        with patch.object(Path, "open", slow_open):
            t0 = time.perf_counter()
            resp = _signed_get(ca, app_a, rid, fid, end=4095)
            elapsed = time.perf_counter() - t0
        assert resp.status_code == 206, resp.text
        assert elapsed < 2.0, f"GET still too slow ({elapsed:.2f}s) — full-file read suspected"
        assert resp.headers.get("Hive-Content-Sha256") == sha


def test_mutation_invalidates_and_get_503(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = b"mut-" * 1024
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        fp = _fp_for_payload(app_a, payload)
        _prepare_digests_db(app_a, fp)
        path = Path(fp.path)
        path.write_bytes(payload + b"extra")
        _forbid_full_file_digest(monkeypatch)
        assert _signed_get(ca, app_a, rid, fid).status_code in (404, 503)


def test_corrupt_cache_not_served(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = b"c" * 2048
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)))
            persist_chunk_digests_corrupt(db, fp, bad_json='["00"]')
            db.commit()
        finally:
            db.close()
        _forbid_full_file_digest(monkeypatch)
        assert _signed_get(ca, app_a, rid, fid).status_code in (200, 206)


def test_hive_off_skips_backfill_io(pair_apps, tmp_path):
    with TestClient(create_app(_settings_pair(tmp_path, "cds_off"))) as client:
        db = client.app.state.session_factory()
        try:
            db.merge(
                Setting(key="hive_enabled", value="false", is_secret=False, updated_at=datetime.now(timezone.utc))
            )
            db.commit()
            assert hive_enabled_in_db(db) is False
            assert chunk_digest_backfill_tick(db, client.app.state.settings) is False
            db.commit()
        finally:
            db.close()


def test_get_without_digests_never_returns_chunks_pending(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = b"z" * 512
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)))
            from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

            clear_chunk_digest_state(fp)
            db.commit()
        finally:
            db.close()
        _forbid_full_file_digest(monkeypatch)
        resp = _signed_get(ca, app_a, rid, fid)
        assert resp.status_code in (200, 206), resp.text
        assert "Hive-Chunks-Pending" not in resp.headers


def test_rescan_same_size_clears_stale_chunk_digests(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = b"rescan-" * 800
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            from mediatracker.db.models import MediaItem
            from mediatracker.hive.chunk_digest_store import chunk_digests_row_valid
            from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item

            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)))
            assert chunk_digests_row_valid(fp) is not None
            item = db.get(MediaItem, fp.media_item_id)
            path = Path(fp.path)
            path.write_bytes(os.urandom(len(payload)))
            os.utime(path, None)
            ensure_fingerprint_row_for_item(db, app_a.state.settings, item, now=NOW)
            db.flush()
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            assert fp.chunk_digests_json is None
            assert fp.sha256_hex is None
            db.commit()
        finally:
            db.close()
        _forbid_full_file_digest(monkeypatch)
        assert _signed_get(ca, app_a, rid, fid).status_code != 503


@_skip_legacy_chunk_backfill
def test_backfill_multi_chunk_over_16m(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = os.urandom(17 * 1024 * 1024)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, sha = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            fp = db.get(HiveFileFingerprint, _fp_for_payload(app_a, payload).media_item_id)
            clear_mod = __import__("mediatracker.hive.chunk_digest_store", fromlist=["clear_chunk_digest_state"])
            clear_mod.clear_chunk_digest_state(fp)
            db.commit()
            run_chunk_backfill_until_ready(db, app_a.state.settings, fp.media_item_id)
            db.commit()
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            digests = json.loads(fp.chunk_digests_json)
            assert digests == chunk_digests_for_file(Path(fp.path), size=len(payload))
            assert len(digests) == 2
        finally:
            db.close()
        _forbid_full_file_digest(monkeypatch)
        resp = _signed_get(ca, app_a, rid, fid, end=1023)
        assert resp.status_code == 206
        assert resp.headers.get("Hive-Content-Sha256") == sha


def _three_chunk_payload() -> bytes:
    """~40 Mio → 3 morceaux, 2ᵉ tick nécessaire avec budget 32 Mio."""
    return os.urandom(TRANSFER_CHUNK_SIZE * 2 + TRANSFER_CHUNK_SIZE // 2)


def _clear_digests_only(db, fp: HiveFileFingerprint) -> None:
    from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

    grant_share_intent(db, int(fp.media_item_id))
    clear_chunk_digest_state(fp)
    db.commit()
    db.expire_all()


@_skip_legacy_chunk_backfill
def test_backfill_oracle_three_chunks_requires_two_ticks(pair_apps):
    app_a, app_b = pair_apps
    payload = _three_chunk_payload()
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _seed_file_on_a(app_a, rid, payload)
    db = app_a.state.session_factory()
    try:
        fp = db.scalar(
            select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)).limit(1)
        )
        _clear_digests_only(db, fp)
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        settings = app_a.state.settings
        assert hive_enabled_in_db(db)
        assert fp.chunk_digests_json is None
        assert fingerprint_needs_chunk_backfill(fp)
        assert list_backfill_candidate_ids(db, limit=5, now=NOW)
        assert chunk_digest_backfill_tick(db, settings, now=NOW, recurse=False) is True
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        assert fp.chunk_digest_offset == TRANSFER_CHUNK_SIZE * 2
        assert fp.chunk_digest_partial_json is not None
        assert fp.chunk_digests_json is None
        assert chunk_digest_backfill_tick(db, settings, now=NOW + timedelta(seconds=1), recurse=False)
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        oracle = chunk_digests_for_file(Path(fp.path), size=len(payload))
        assert json.loads(fp.chunk_digests_json) == oracle
        assert len(oracle) == 3
        db.commit()
    finally:
        db.close()


@_skip_legacy_chunk_backfill
def test_backfill_resumes_after_memory_cleared_between_ticks(pair_apps):
    app_a, app_b = pair_apps
    payload = _three_chunk_payload()
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(
                select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)).limit(1)
            )
            _clear_digests_only(db, fp)
            settings = app_a.state.settings
            chunk_digest_backfill_tick(db, settings, now=NOW)
            db.commit()
            clear_active_chunk_session()
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            fp.chunk_digest_claimed_at = None
            db.commit()
            run_chunk_backfill_until_ready(db, settings, fp.media_item_id)
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            assert json.loads(fp.chunk_digests_json) == chunk_digests_for_file(
                Path(fp.path), size=len(payload)
            )
            db.commit()
        finally:
            db.close()


@_skip_legacy_chunk_backfill
def test_backfill_rejects_mutation_between_ticks(pair_apps):
    app_a, app_b = pair_apps
    payload = _three_chunk_payload()
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            from mediatracker.db.models import MediaItem

            fp = db.scalar(
                select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)).limit(1)
            )
            item = db.get(MediaItem, fp.media_item_id)
            _clear_digests_only(db, fp)
            settings = app_a.state.settings
            chunk_digest_backfill_tick(db, settings, now=NOW)
            path = Path(fp.path)
            path.write_bytes(os.urandom(len(payload)))
            os.utime(path, None)
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW + timedelta(seconds=2))
            db.commit()
            chunk_digest_backfill_tick(db, settings, now=NOW + timedelta(seconds=3))
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            assert fp.sha256_hex is None
            assert fp.chunk_digests_json is None
            assert fp.chunk_digest_claimed_at is None
            db.commit()
        finally:
            db.close()


@_skip_legacy_chunk_backfill
def test_backfill_yielded_when_fingerprint_daemon_claims_sha(pair_apps):
    app_a, app_b = pair_apps
    payload = _three_chunk_payload()
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _seed_file_on_a(app_a, rid, payload)
    db = app_a.state.session_factory()
    try:
        fp = db.scalar(
            select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)).limit(1)
        )
        _clear_digests_only(db, fp)
        settings = app_a.state.settings
        chunk_digest_backfill_tick(db, settings, now=NOW, recurse=False)
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        assert fp.chunk_digest_claimed_at is not None
        fp.sha256_hex = None
        fp.partial_size = 0
        enqueue_fingerprint_work(db, int(fp.media_item_id), now=NOW)
        db.commit()
        fingerprint_daemon_tick(db, settings, now=NOW)
        db.commit()
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        assert fp.chunk_digest_claimed_at is None
    finally:
        db.close()


def _same_utc_moment(stored: datetime | None, expected: datetime) -> bool:
    if stored is None:
        return False
    if stored.tzinfo is None:
        stored = stored.replace(tzinfo=timezone.utc)
    exp = expected if expected.tzinfo else expected.replace(tzinfo=timezone.utc)
    return stored == exp


def _seed_fingerprint_row(
    db,
    app,
    settings,
    *,
    tmdb_id: int,
    payload: bytes,
    updated_at: datetime,
    ruche_id: bytes | None = None,
) -> HiveFileFingerprint:
    from mediatracker.core.storage import storage_layout
    from mediatracker.db.models import Media, MediaItem
    from mediatracker.hive.catalog import publish_available
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.library.scanner import remember_filed

    layout = storage_layout(db, settings)
    folder = layout.movies_dir / f"m-{tmdb_id}"
    folder.mkdir(parents=True, exist_ok=True)
    video = folder / "v.bin"
    video.write_bytes(payload)
    media = Media(type="movie", title="M", year=2020, tmdb_id=tmdb_id)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="downloaded", downloaded=True, file_path=str(video.resolve()))
    db.add(item)
    db.flush()
    remember_filed(db, video.resolve(), media, item, updated_at)
    sha = file_sha256_hex(video)
    fp = HiveFileFingerprint(
        media_item_id=item.id,
        path=str(video.resolve()),
        size=len(payload),
        mtime_ns=video.stat().st_mtime_ns,
        sha256_hex=sha,
        partial_size=len(payload),
        updated_at=updated_at,
    )
    db.merge(fp)
    db.flush()
    grant_share_intent(db, int(item.id))
    if ruche_id is not None:
        publish_available(
            db,
            settings,
            ruche_id=ruche_id,
            media=media,
            item=item,
            fingerprint_hex=sha,
            now=updated_at,
        )
    row = db.get(HiveFileFingerprint, item.id)
    assert row is not None
    persist_chunk_digests_for_test_file(db, row)
    row.updated_at = updated_at
    db.flush()
    return row


@_skip_legacy_chunk_backfill
def test_backfill_idle_pass_no_fingerprint_writes(pair_apps):
    """Passage à vide : 129 lignes valides → aucun UPDATE sur hive_file_fingerprint."""
    app_a, _ = pair_apps
    stamp = NOW - timedelta(days=500)
    with TestClient(app_a) as ca:
        _enable(ca, "aa", URL_A)
    db = app_a.state.session_factory()
    try:
        settings = app_a.state.settings
        stamps: dict[int, datetime] = {}
        for i in range(129):
            fp = _seed_fingerprint_row(
                db, app_a, settings, tmdb_id=97000 + i, payload=bytes([i % 251]) * 256, updated_at=stamp
            )
            stamps[int(fp.media_item_id)] = stamp
        db.commit()
        assert count_pending_chunk_backfill(db) == 0
        assert list_backfill_candidate_ids(db, limit=32, now=NOW) == []
        chunk_digest_backfill_tick(db, settings, now=NOW, recurse=False)
        db.flush()
        for mid, expected in stamps.items():
            row = db.get(HiveFileFingerprint, mid)
            assert row is not None
            assert _same_utc_moment(row.updated_at, expected)
        db.rollback()
    finally:
        db.close()


@_skip_legacy_chunk_backfill
def test_backfill_not_masked_by_129_valid_rows(pair_apps):
    """N1 : 129 valides + 1 à réparer : scan sans mutation des valides, count et sélection corrects."""
    app_a, _ = pair_apps
    old_moment = NOW - timedelta(days=400)
    newer_moment = NOW - timedelta(days=1)
    with TestClient(app_a) as ca:
        _enable(ca, "aa", URL_A)
    db = app_a.state.session_factory()
    try:
        settings = app_a.state.settings
        assert hive_enabled_in_db(db)
        stamps: dict[int, datetime] = {}
        for i in range(129):
            fp = _seed_fingerprint_row(
                db, app_a, settings, tmdb_id=97100 + i, payload=bytes([i % 251]) * 256, updated_at=old_moment
            )
            stamps[int(fp.media_item_id)] = old_moment
        repair_payload = b"needs-repair" * 200
        repair = _seed_fingerprint_row(
            db, app_a, settings, tmdb_id=97999, payload=repair_payload, updated_at=newer_moment
        )
        repair_id = int(repair.media_item_id)
        repair.chunk_digests_sha256_hex = "0" * 64
        repair.chunk_digest_claimed_at = None
        db.flush()
        assert fingerprint_needs_chunk_backfill(repair)
        assert count_pending_chunk_backfill(db) == 1
        picked = list_backfill_candidate_ids(db, limit=1, now=NOW)
        assert repair_id in picked
        chunk_digest_backfill_tick(db, settings, now=NOW, recurse=False)
        db.flush()
        for mid, expected in stamps.items():
            row = db.get(HiveFileFingerprint, mid)
            assert _same_utc_moment(row.updated_at, expected)
        db.rollback()
    finally:
        db.close()


@_skip_legacy_chunk_backfill
def test_corrupt_complete_json_selected_for_backfill(pair_apps):
    app_a, _ = pair_apps
    with TestClient(app_a) as ca:
        _enable(ca, "aa", URL_A)
    db = app_a.state.session_factory()
    try:
        settings = app_a.state.settings
        payload = b"z" * (TRANSFER_CHUNK_SIZE + 512)
        fp = _seed_fingerprint_row(db, app_a, settings, tmdb_id=96900, payload=payload, updated_at=NOW)
        fp.chunk_digests_json = '["00"]'
        fp.chunk_digest_offset = int(fp.size)
        fp.chunk_digests_sha256_hex = (fp.sha256_hex or "").strip()
        db.flush()
        assert fingerprint_needs_chunk_backfill(fp)
        assert int(fp.media_item_id) in list_backfill_candidate_ids(db, limit=8, now=NOW)
        assert count_pending_chunk_backfill(db) >= 1
        db.commit()
    finally:
        db.close()


@_skip_legacy_chunk_backfill
def test_backfill_partial_cas_rejects_rescan_during_io(pair_apps):
    app_a, _ = pair_apps
    payload = _three_chunk_payload()
    with TestClient(app_a) as ca:
        _enable(ca, "aa", URL_A)
    db = app_a.state.session_factory()
    try:
        from mediatracker.db.models import MediaItem

        fp = _seed_fingerprint_row(db, app_a, app_a.state.settings, tmdb_id=96850, payload=payload, updated_at=NOW)
        _clear_digests_only(db, fp)
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        settings = app_a.state.settings
        assert chunk_digest_backfill_tick(db, settings, now=NOW, recurse=False)
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        assert fp.chunk_digest_partial_json
        item = db.get(MediaItem, fp.media_item_id)
        path = Path(fp.path)
        path.write_bytes(os.urandom(len(payload)))
        os.utime(path, None)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW + timedelta(seconds=1))
        db.commit()
        clear_active_chunk_session()
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        fp.chunk_digest_claimed_at = None
        db.commit()
        chunk_digest_backfill_tick(db, settings, now=NOW + timedelta(seconds=2), recurse=False)
        fp = db.get(HiveFileFingerprint, fp.media_item_id)
        assert fp.sha256_hex is None or fp.chunk_digests_json is None
        if (fp.sha256_hex or "").strip():
            assert chunk_digests_row_valid(fp) is None
        db.commit()
    finally:
        db.close()


def test_fingerprint_daemon_persists_chunk_digests_oracle(pair_apps):
    app_a, _ = pair_apps
    payload = _three_chunk_payload()
    with TestClient(app_a) as ca:
        _enable(ca, "aa", URL_A)
    db = app_a.state.session_factory()
    try:
        settings = app_a.state.settings
        fp = _seed_fingerprint_row(db, app_a, settings, tmdb_id=96840, payload=payload, updated_at=NOW)
        mid = int(fp.media_item_id)
        item = db.get(MediaItem, mid)
        assert item is not None
        fp.sha256_hex = None
        fp.partial_size = 0
        fp.partial_sha256_hex = None
        from mediatracker.hive.chunk_digest_store import clear_chunk_digest_state

        clear_chunk_digest_state(fp)
        ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
        enqueue_fingerprint_work(db, mid, now=NOW)
        db.commit()
        for t in range(500):
            fingerprint_daemon_tick(db, settings, now=NOW + timedelta(seconds=t))
            db.commit()
            fp = db.get(HiveFileFingerprint, mid)
            if (fp.sha256_hex or "").strip():
                break
        assert (fp.sha256_hex or "").strip()
        assert fp.chunk_digests_json is None
    finally:
        db.close()


@_skip_legacy_chunk_backfill
def test_stale_bound_sha_repaired_by_backfill(pair_apps, monkeypatch):
    app_a, app_b = pair_apps
    payload = b"x" * 4096
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        _enable(ca, "aa", URL_A)
        _enable(cb, "bb", URL_A)
        rid = _join_ruche(ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            fp = db.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.size == len(payload)))
            _prepare_digests_db(app_a, fp)
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            media_item_id = int(fp.media_item_id)
            fp.chunk_digests_sha256_hex = "0" * 64
            assert fingerprint_needs_chunk_backfill(fp)
            db.commit()
        finally:
            db.close()
        _forbid_full_file_digest(monkeypatch)
        assert _signed_get(ca, app_a, rid, fid).status_code == 503
        db = app_a.state.session_factory()
        try:
            run_chunk_backfill_until_ready(db, app_a.state.settings, media_item_id)
            fp = db.get(HiveFileFingerprint, fp.media_item_id)
            oracle = chunk_digests_for_file(Path(fp.path), size=len(payload))
            assert chunk_digests_row_valid(fp) == oracle
            db.commit()
        finally:
            db.close()
        assert _signed_get(ca, app_a, rid, fid).status_code == 206

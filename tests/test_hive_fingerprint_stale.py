"""Revalidation empreinte : course lecture disque / writer SQLite concurrent."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.test_hive_catalog_sharing_worker import _enable_hive

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import HiveFileFingerprint, Media, MediaItem
from mediatracker.hive import fingerprint as fp_mod

NOW = datetime(2026, 11, 8, 19, 0, tzinfo=timezone.utc)

pytest_plugins = ["tests.test_hive_t1"]


@pytest.fixture(autouse=True)
def _reset_fingerprint_daemon_session():
    from mediatracker.hive import fingerprint_daemon as daemon_mod

    daemon_mod.clear_active_session()
    yield
    daemon_mod.clear_active_session()

CONCURRENT_MTIME_NS = 9_001_002_003_004_005


def _apply_hash_result_legacy_cached(db, snapshot, *, read_upto, digest, moment):
    """Version défectueuse : cache ORM + pas de revalidation ni UPDATE conditionnel (RED)."""
    row = db.get(HiveFileFingerprint, snapshot.media_item_id)
    if row is None:
        return
    if read_upto >= snapshot.size:
        row.sha256_hex = digest.hexdigest()
        row.partial_size = snapshot.size
    elif read_upto > snapshot.partial_start:
        row.partial_size = read_upto
    row.updated_at = moment


def _apply_hash_result_orm_without_conditional(db, snapshot, *, read_upto, digest, moment):
    """RED intermédiaire : revalidation OK mais écriture ORM non atomique."""
    path = Path(snapshot.path)
    if not fp_mod._disk_matches_snapshot(path, snapshot):
        return
    row = fp_mod._load_fingerprint_row_fresh(db, snapshot.media_item_id)
    if not fp_mod._snapshot_still_valid(row, snapshot):
        return
    assert row is not None
    fp_mod._apply_pre_write_barrier()
    if read_upto >= snapshot.size:
        row.sha256_hex = digest.hexdigest()
        row.partial_size = snapshot.size
    elif read_upto > snapshot.partial_start:
        row.partial_size = read_upto
    row.updated_at = moment


def test_fingerprint_rejects_stale_hash_when_file_changes_during_read(client, app, settings, monkeypatch):
    """Scénario historique : pause lecture streaming + reset DB → pas de SHA final."""
    from tests.test_hive_fingerprint_daemon_concurrency import (
        test_daemon_read_pause_db_reset_rejects_final_sha,
    )

    _enable_hive(client)
    test_daemon_read_pause_db_reset_rejects_final_sha(client, app, settings, monkeypatch)


def test_fingerprint_red_proves_green_assertions_fail_under_legacy(client, app, settings):
    """RED : apply legacy en isolation ignore l'invalidation concurrente en base."""
    file_len = 256
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "red-meta.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    content = b"R" * file_len
    video.write_bytes(content)
    digest = hashlib.sha256(content)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="RedMeta", year=2026, tmdb_id=90)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        size, mtime_ns = fp_mod._stat_fingerprint(video)
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=size,
                mtime_ns=mtime_ns,
                partial_size=0,
                updated_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
        snapshot = fp_mod._FingerprintWorkSnapshot(
            media_item_id=item_id,
            path=str(video.resolve()),
            size=size,
            mtime_ns=mtime_ns,
            partial_start=0,
            partial_sha256_hex=None,
        )
    finally:
        db.close()

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

    legacy_db = app.state.session_factory()
    try:
        _apply_hash_result_legacy_cached(
            legacy_db, snapshot, read_upto=size, digest=digest, moment=NOW
        )
        legacy_db.commit()
        legacy_sha = legacy_db.get(HiveFileFingerprint, item_id).sha256_hex
    finally:
        legacy_db.close()

    green_db = app.state.session_factory()
    try:
        row = green_db.get(HiveFileFingerprint, item_id)
        assert row is not None
        row.sha256_hex = None
        row.partial_size = 0
        green_db.commit()
        fp_mod._apply_hash_result(
            green_db, snapshot, read_upto=size, digest=digest, moment=NOW
        )
        green_db.commit()
        green_sha = green_db.get(HiveFileFingerprint, item_id).sha256_hex
    finally:
        green_db.close()

    assert legacy_sha == digest.hexdigest()
    assert green_sha is None


def test_fingerprint_red_legacy_cached_apply_writes_stale_sha(client, app, settings):
    """RED : sans relecture/revalidation, un hash finalisé périmé peut être commis."""
    test_fingerprint_red_proves_green_assertions_fail_under_legacy(client, app, settings)


def test_fingerprint_barrier_after_verify_before_conditional_write(client, app, settings, monkeypatch):
    from tests.test_hive_fingerprint_daemon_concurrency import (
        test_daemon_barrier_db_mtime_mutation_blocks_stale_sha,
    )

    _enable_hive(client)
    test_daemon_barrier_db_mtime_mutation_blocks_stale_sha(client, app, settings, monkeypatch)


def test_fingerprint_red_orm_without_conditional_writes_despite_partial_reset(client, app, settings):
    """RED : apply ORM non conditionnel commit un hash malgré reset concurrent de partial."""
    file_len = 128
    layout = storage_layout(app.state.session_factory(), settings)
    video = layout.movies_dir / "red-orm.mkv"
    video.parent.mkdir(parents=True, exist_ok=True)
    content = b"O" * file_len
    video.write_bytes(content)
    digest = hashlib.sha256(content)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="RedOrm", year=2026, tmdb_id=91)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(video))
        db.add(item)
        db.flush()
        size, mtime_ns = fp_mod._stat_fingerprint(video)
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=size,
                mtime_ns=mtime_ns,
                partial_size=file_len,
                updated_at=NOW,
            )
        )
        db.commit()
        item_id = item.id
        snapshot = fp_mod._FingerprintWorkSnapshot(
            media_item_id=item_id,
            path=str(video.resolve()),
            size=size,
            mtime_ns=mtime_ns,
            partial_start=file_len,
            partial_sha256_hex=digest.hexdigest(),
        )
    finally:
        db.close()

    other = app.state.session_factory()
    try:
        fp = other.get(HiveFileFingerprint, item_id)
        assert fp is not None
        fp.partial_size = 0
        fp.sha256_hex = None
        other.commit()
    finally:
        other.close()

    bad_db = app.state.session_factory()
    try:
        _apply_hash_result_orm_without_conditional(
            bad_db, snapshot, read_upto=size, digest=digest, moment=NOW
        )
        bad_db.commit()
        assert bad_db.get(HiveFileFingerprint, item_id).sha256_hex == digest.hexdigest()
    finally:
        bad_db.close()

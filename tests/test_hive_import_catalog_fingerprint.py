"""Réception Hive : réutilisation de l'empreinte catalogue après rangement."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from sqlalchemy import select
from tests.test_hive_t5 import NOW
from tests.test_video_integrity import _minimal_mkv

from mediatracker.core.pipeline.queue import _import_one
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveFileFingerprint,
    HiveFingerprintQueue,
    HiveShareIntent,
    Media,
    MediaItem,
)
from mediatracker.hive.fingerprint import record_catalog_fingerprint_for_local_file
from mediatracker.hive.fingerprint_daemon import compute_fingerprint_sleep
from mediatracker.hive.fingerprint_load import system_pressure_score

NOW_UTC = NOW


def test_record_catalog_fingerprint_without_share_intent(client, app, tmp_path):
    payload = _minimal_mkv()
    sha = hashlib.sha256(payload).hexdigest()
    dest = tmp_path / "piece.mkv"
    dest.write_bytes(payload)
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Demo", year=2020, tmdb_id=92001, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
        record_catalog_fingerprint_for_local_file(
            db, item, dest, sha256_hex=sha, now=NOW_UTC
        )
        db.flush()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert fp.sha256_hex == sha
        assert int(fp.size) == len(payload)
        assert fp.mtime_ns is not None
        assert db.get(HiveShareIntent, item.id) is None
        assert db.scalar(select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == item.id)) is None
    finally:
        db.close()


def test_import_one_records_hive_fingerprint_at_destination(client, app, tmp_path):
    payload = _minimal_mkv()
    sha = hashlib.sha256(payload).hexdigest()
    source = tmp_path / "incoming.mkv"
    source.write_bytes(payload)
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media = Media(type="film", title="Demo", year=2020, tmdb_id=92002, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloading")
        db.add(item)
        db.flush()
        row = Download(
            media_item_id=item.id,
            state="post_processing",
            filename=source.name,
            error=str(source),
            created_at=NOW_UTC,
        )
        db.add(row)
        db.flush()
        ctx = DownloadHiveFetch(
            download_id=row.id,
            sha256_hex=sha,
            sources_json="[]",
            active_source_index=0,
            ruche_id=os.urandom(16),
            file_id=os.urandom(16),
            origin_instance_id="aa",
            peer_base_url="https://peer.example",
            expected_size=len(payload),
            content_sha256_hex=sha,
            created_at=NOW_UTC,
            updated_at=NOW_UTC,
        )
        db.add(ctx)
        db.commit()
        _import_one(db, row, NOW_UTC, settings)
        db.flush()
        assert row.state == "done"
        dest = Path(item.file_path or "")
        assert dest.is_file()
        fp = db.get(HiveFileFingerprint, item.id)
        assert fp is not None
        assert fp.sha256_hex == sha
        assert fp.path == str(dest.resolve())
        assert int(fp.size) == dest.stat().st_size
        assert db.get(HiveShareIntent, item.id) is None
    finally:
        db.close()


def test_adaptive_sleep_fallback_and_bounds(client, app, monkeypatch):
    db = app.state.session_factory()
    try:
        monkeypatch.setattr(
            "mediatracker.hive.fingerprint_load.system_pressure_score",
            lambda: None,
        )
        assert compute_fingerprint_sleep(db) == 30.0

        monkeypatch.setattr(
            "mediatracker.hive.fingerprint_load.system_pressure_score",
            lambda: 0.9,
        )
        from mediatracker.db.models import HiveFileFingerprint, Media, MediaItem
        from mediatracker.hive.fingerprint_queue import enqueue_fingerprint_work

        media = Media(type="film", title="Demo", year=2020)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id)
        db.add(item)
        db.flush()
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path="/tmp/x",
                size=1,
                mtime_ns=1,
                updated_at=NOW_UTC,
            )
        )
        enqueue_fingerprint_work(db, item.id, now=NOW_UTC)
        db.flush()
        assert compute_fingerprint_sleep(db) == 8.0
    finally:
        db.close()
    assert system_pressure_score() is None or 0.0 <= (system_pressure_score() or 0) <= 1.0

"""Grant : pas de PeerRateLimiter ; 429 Hive → pending ; file par pair."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import select
from starlette.testclient import TestClient
from tests.test_hive_fetch_grant_lot1 import _grant_headers, _mint, _setup_pair
from tests.test_hive_t5 import NOW, URL_A, URL_B, _seed_file_on_a

from mediatracker.core.pipeline.constants import MAX_ATTEMPTS
from mediatracker.core.pipeline.queue import _apply_status
from mediatracker.db.models import Download, DownloadHiveFetch, Media, MediaItem, Setting
from mediatracker.hive.download_aria2 import (
    HIVE_PEER_BUSY_MESSAGE,
    active_hive_fetches_for_peer,
    prepare_hive_aria2_unlock,
)
from mediatracker.hive.download_pipeline import (
    clear_hive_peer_busy_strike,
    defer_hive_peer_busy,
    hive_peer_rate_failure,
)
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.providers.base import DownloadStatus

pytest_plugins = ["tests.test_hive_t5"]


def test_grant_range_requests_do_not_hit_peer_rate_limit(transfer_pair):
    app_a, app_b = transfer_pair
    app_a.state.hive_peer_limiter = PeerRateLimiter(limit=2, window_seconds=60.0)
    payload = b"x" * 4096
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        grant = _mint(ca, app_b, rid, fid)
        fpath = f"/hive/files/{fid}"
        headers = _grant_headers(rid, grant)
        for start in range(0, len(payload), 512):
            end = min(start + 511, len(payload) - 1)
            resp = ca.get(fpath, headers={**headers, "Range": f"bytes={start}-{end}"})
            assert resp.status_code in (200, 206), resp.text


def test_hive_aria2_429_stays_pending_not_failed(client):
    from tests.test_hive_aria2_source_unreachable import _seed_hive_download, _TrackingDownloader

    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    try:
        row = _seed_hive_download(db, title="Demo Epsilon")
        downloader = _TrackingDownloader()
        status = DownloadStatus(
            state="error",
            error="aria2 a échoué : status=429 Too Many Requests",
            done_bytes=50,
            total_bytes=1024,
            speed=None,
            path=None,
        )
        _apply_status(db, row, status, now, downloader, settings=settings)
        assert row.state == "pending"
        assert row.error_code == "HIVE_PEER_BUSY"
        assert row.error == HIVE_PEER_BUSY_MESSAGE
        assert row.next_retry_at is not None
        assert downloader.delete_flags == [False]
    finally:
        db.close()


def test_hive_peer_rate_failure_detector():
    assert hive_peer_rate_failure("HTTP 429 Too Many Requests")
    assert hive_peer_rate_failure("status=429")
    assert not hive_peer_rate_failure("HTTP 502 Bad Gateway")


def test_third_hive_download_waits_for_peer_slot(client):
    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        db.merge(Setting(key="hive_transfer_parallel", value="2", is_secret=False, updated_at=NOW))
        for i in range(3):
            media = Media(type="film", title=f"Slot {i}", year=2020, tmdb_id=7800 + i, followed=True)
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, state="downloading")
            db.add(item)
            db.flush()
            row = Download(media_item_id=item.id, state="downloading" if i < 2 else "pending", created_at=now)
            db.add(row)
            db.flush()
            db.add(
                DownloadHiveFetch(
                    download_id=row.id,
                    sha256_hex=f"{i:064x}",
                    sources_json="[]",
                    active_source_index=0,
                    ruche_id=os.urandom(16),
                    file_id=os.urandom(16),
                    origin_instance_id="aa",
                    peer_base_url=URL_A,
                    expected_size=100,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        assert active_hive_fetches_for_peer(db, URL_A) == 2
        pending = db.scalar(select(Download).where(Download.state == "pending").limit(1))
        assert pending is not None
        from mediatracker.hive.transfer_settings import transfer_parallel_limit

        assert transfer_parallel_limit(db) == 2
        blocked = active_hive_fetches_for_peer(db, URL_A, exclude_download_id=pending.id) >= transfer_parallel_limit(
            db
        )
        assert blocked
    finally:
        db.close()


def test_transfer_parallel_gate_unit():
    gate = TransferParallelGate()
    assert gate.acquire(1, 2)
    assert gate.acquire(2, 2)
    assert not gate.acquire(3, 2)
    assert gate.acquire(1, 2)
    gate.release(1)
    assert gate.active_count() == 2
    gate.release(1)
    assert gate.acquire(3, 2)
    gate.release(2)
    gate.release(3)
    assert gate.active_count() == 0


def test_transfer_parallel_gate_refcount_same_grant():
    gate = TransferParallelGate()
    assert gate.acquire(42, 2)
    assert gate.acquire(42, 2)
    assert gate.active_count() == 1
    gate.release(42)
    assert gate.active_count() == 1
    gate.release(42)
    assert gate.active_count() == 0


def test_hive_peer_busy_never_increments_attempts():
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    row = Download(id=99, media_item_id=1, state="downloading", attempts=2)
    clear_hive_peer_busy_strike(99)
    attempts_before = row.attempts
    for _ in range(MAX_ATTEMPTS + 5):
        defer_hive_peer_busy(row, now)
        assert row.state == "pending"
        assert row.attempts == attempts_before
        now = row.next_retry_at or now
    assert row.error_code == "HIVE_PEER_BUSY"


def test_prepare_hive_unlock_defers_before_mint(client, monkeypatch):
    db = client.app.state.session_factory()
    settings = client.app.state.settings
    now = datetime.now(timezone.utc)
    mint_calls: list[int] = []

    def _track_mint(*_args, **kwargs):
        mint_calls.append(1)
        raise AssertionError("mint should not run when peer queue is full")

    monkeypatch.setattr("mediatracker.hive.download_aria2.ensure_mint_for_download", _track_mint)
    try:
        db.merge(Setting(key="hive_transfer_parallel", value="1", is_secret=False, updated_at=NOW))
        for i in range(2):
            media = Media(type="film", title=f"Defer {i}", year=2020, tmdb_id=7900 + i, followed=True)
            db.add(media)
            db.flush()
            item = MediaItem(media_id=media.id, state="downloading")
            db.add(item)
            db.flush()
            row = Download(media_item_id=item.id, state="downloading" if i == 0 else "pending", created_at=now)
            db.add(row)
            db.flush()
            db.add(
                DownloadHiveFetch(
                    download_id=row.id,
                    sha256_hex=f"{i:064x}",
                    sources_json="[]",
                    active_source_index=0,
                    ruche_id=os.urandom(16),
                    file_id=os.urandom(16),
                    origin_instance_id="aa",
                    peer_base_url=URL_A,
                    expected_size=100,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
        pending = db.scalar(select(Download).where(Download.state == "pending").limit(1))
        assert pending is not None
        item = db.get(MediaItem, pending.media_item_id)
        media = db.get(Media, item.media_id)
        match = MagicMock()
        match.sha256_hex = "f" * 64
        match.expected_size = 100
        match.sources = [{"ruche_id_hex": "00" * 16, "file_id_hex": "11" * 16, "origine": "aa"}]
        match.cle = {"nom": "demo.mkv"}
        resolution = MagicMock(outcome="queued", match=match)
        monkeypatch.setattr(
            "mediatracker.hive.source_download.resolve_hive_match_for_item",
            lambda *a, **k: resolution,
        )
        monkeypatch.setattr(
            "mediatracker.hive.download_aria2.attach_or_refresh_hive_fetch",
            lambda db, settings, download, match, now: db.get(DownloadHiveFetch, download.id),
        )
        outcome = prepare_hive_aria2_unlock(
            db,
            settings,
            row=pending,
            item=item,
            media=media,
            now=now,
            defer_final=False,
        )
        assert outcome == "waiting"
        assert mint_calls == []
        assert pending.error_code == "HIVE_PEER_BUSY"
        delay = (pending.next_retry_at - now).total_seconds()
        assert delay <= 60
    finally:
        db.close()


def test_source_rejects_third_grant_when_parallel_full(transfer_pair):
    app_a, app_b = transfer_pair
    gate = TransferParallelGate()
    app_a.state.hive_transfer_parallel = gate
    app_a.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
    payload = b"z" * 2048
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        db = app_a.state.session_factory()
        try:
            db.merge(Setting(key="hive_transfer_parallel", value="2", is_secret=False, updated_at=NOW))
            db.commit()
        finally:
            db.close()
        grant_ids = []
        from mediatracker.db.models import HiveFetchGrant

        for _ in range(2):
            grant = _mint(ca, app_b, rid, fid)
            grant_ids.append(grant)
        grant_busy = _mint(ca, app_b, rid, fid)
        db2 = app_a.state.session_factory()
        try:
            rows = list(db2.scalars(select(HiveFetchGrant).order_by(HiveFetchGrant.id)).all())
        finally:
            db2.close()
        assert len(rows) >= 3
        gate.acquire(int(rows[0].id), 2)
        gate.acquire(int(rows[1].id), 2)
        fpath = f"/hive/files/{fid}"
        resp = ca.get(fpath, headers=_grant_headers(rid, grant_busy))
        assert resp.status_code == 429


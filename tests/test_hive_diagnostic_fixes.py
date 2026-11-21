"""Correctifs diagnostic « Source Hive occupée » (21 nov. 2026)."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes
from tests.test_hive_fetch_grant_lot1 import _grant_headers, _mint, _setup_pair
from tests.test_hive_t5 import NOW, URL_A, _seed_file_on_a

from mediatracker.db.models import Download, DownloadHiveFetch, HiveReceiveQuota, HiveRuche, Media, MediaItem, Setting
from mediatracker.hive.download_aria2 import (
    local_parallel_wait_message,
    prepare_hive_aria2_unlock,
)
from mediatracker.hive.transfer_parallel import STALE_PARALLEL_SLOT_SECONDS, TransferParallelGate

pytest_plugins = ["tests.test_hive_t5"]


def test_transfer_parallel_gate_purges_stale_slots():
    gate = TransferParallelGate()
    clock = {"t": 1000.0}
    gate._clock = lambda: clock["t"]
    assert gate.acquire(7, 2)
    assert gate.active_count() == 1
    clock["t"] = 1000.0 + STALE_PARALLEL_SLOT_SECONDS + 1.0
    assert gate.acquire(8, 1)
    assert gate.active_count() == 1
    assert not gate.is_grant_active(7)


def test_range_416_does_not_hold_parallel_slot(transfer_pair):
    app_a, app_b = transfer_pair
    gate = TransferParallelGate()
    app_a.state.hive_transfer_parallel = gate
    payload = b"x" * catalog_min_logical_video_bytes()
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        grant = _mint(ca, app_b, rid, fid)
        beyond = len(payload) + 50
        bad_range = _grant_headers(rid, grant) | {"Range": f"bytes={beyond}-{beyond + 10}"}
        resp = ca.get(f"/hive/files/{fid}", headers=bad_range)
        assert resp.status_code == 416
        assert gate.active_count() == 0


def test_quota_bytes_counted_on_send_not_on_prepare(transfer_pair):
    app_a, app_b = transfer_pair
    gate = TransferParallelGate()
    app_a.state.hive_transfer_parallel = gate
    app_a.state.hive_send_bandwidth = None
    size = catalog_min_logical_video_bytes()
    payload = b"q" * size
    now = datetime.now(timezone.utc)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        instance_b = cb.get("/api/v1/hive/identity").json()["instance_id"]
        db = app_a.state.session_factory()
        try:
            db.merge(Setting(key="hive_transfer_window_start", value="0", is_secret=False, updated_at=NOW))
            db.merge(Setting(key="hive_transfer_window_end", value="1440", is_secret=False, updated_at=NOW))
            ruche = db.get(HiveRuche, rid)
            ruche.weekly_quota_bytes = size * 3
            from mediatracker.hive.transfer_settings import week_start_date

            ws = week_start_date(now)
            db.merge(
                HiveReceiveQuota(
                    ruche_id=rid,
                    member_instance_id=instance_b,
                    week_start=ws,
                    bytes_used=0,
                )
            )
            db.commit()
        finally:
            db.close()
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        grant = _mint(ca, app_b, rid, fid)
        half = size // 2
        headers = _grant_headers(rid, grant) | {"Range": f"bytes=0-{half - 1}"}
        resp = ca.get(f"/hive/files/{fid}", headers=headers)
        assert resp.status_code == 206
        assert len(resp.content) == half
        db2 = app_a.state.session_factory()
        try:
            row = db2.get(HiveReceiveQuota, (rid, instance_b, ws))
            assert row is not None
            assert int(row.bytes_used) == half
        finally:
            db2.close()
        grant2 = _mint(ca, app_b, rid, fid)
        headers2 = _grant_headers(rid, grant2) | {"Range": f"bytes={half}-{size - 1}"}
        resp2 = ca.get(f"/hive/files/{fid}", headers=headers2)
        assert resp2.status_code == 206
        assert len(resp2.content) == size - half
        db3 = app_a.state.session_factory()
        try:
            row = db3.get(HiveReceiveQuota, (rid, instance_b, ws))
            assert int(row.bytes_used) == size
        finally:
            db3.close()


def test_local_parallel_message_in_prepare_hive(client, monkeypatch):
    from unittest.mock import MagicMock

    db = client.app.state.session_factory()
    now = datetime.now(timezone.utc)
    try:
        db.merge(Setting(key="hive_transfer_parallel", value="1", is_secret=False, updated_at=NOW))
        media = Media(type="film", title="Local queue", year=2020, tmdb_id=88100, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloading")
        db.add(item)
        db.flush()
        row = Download(media_item_id=item.id, state="pending", created_at=now)
        db.add(row)
        db.flush()
        active = Download(media_item_id=item.id, state="downloading", created_at=now)
        db.add(active)
        db.flush()
        db.add(
            DownloadHiveFetch(
                download_id=active.id,
                sha256_hex="b" * 64,
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
        db.add(
            DownloadHiveFetch(
                download_id=row.id,
                sha256_hex="b" * 64,
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
        ctx = db.get(DownloadHiveFetch, row.id)
        msg = local_parallel_wait_message(db, row, URL_A, origin_instance_id="aa")
        assert "En attente" in msg
        assert "1 transfert" in msg
        assert "Instance aa" in msg
        item = db.get(MediaItem, row.media_item_id)
        media = db.get(Media, item.media_id)
        match = MagicMock()
        match.sha256_hex = "b" * 64
        match.expected_size = 100
        match.sources = [{"ruche_id_hex": "00" * 16, "file_id_hex": "11" * 16, "origine": "aa"}]
        match.cle = {"nom": "demo.mkv"}
        monkeypatch.setattr(
            "mediatracker.hive.source_download.resolve_hive_match_for_item",
            lambda *a, **k: MagicMock(outcome="queued", match=match),
        )
        monkeypatch.setattr(
            "mediatracker.hive.download_aria2.attach_or_refresh_hive_fetch",
            lambda db, settings, download, match, now: db.get(DownloadHiveFetch, download.id),
        )
        outcome = prepare_hive_aria2_unlock(
            db,
            client.app.state.settings,
            row=row,
            item=item,
            media=media,
            now=now,
            defer_final=False,
        )
        assert outcome == "waiting"
        assert row.error is not None
        assert "En attente" in row.error
        assert "transferts en cours" in row.error
        del ctx
    finally:
        db.close()
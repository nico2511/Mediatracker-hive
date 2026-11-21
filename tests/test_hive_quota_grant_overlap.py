"""Quota envoyeur Hive : pas de surcompte quand plusieurs Range partagent un grant."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes
from tests.test_hive_fetch_grant_lot1 import _file_path, _grant_headers, _mint, _setup_pair
from tests.test_hive_t5 import NOW, _seed_file_on_a

from mediatracker.db.models import HiveFetchGrant, HiveReceiveQuota, HiveRuche, Setting
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.hive.transfer_settings import week_start_date

pytest_plugins = ["tests.test_hive_t5"]


def test_quota_sums_range_bytes_when_parallel_slot_held(transfer_pair):
    """Deux GET Range sur le même grant avec un slot parallel déjà tenu (refcount > 0)."""
    app_a, app_b = transfer_pair
    gate = TransferParallelGate()
    app_a.state.hive_transfer_parallel = gate
    app_a.state.hive_send_bandwidth = None

    size = catalog_min_logical_video_bytes()
    payload = b"q" * size
    half = size // 2
    now = datetime.now(timezone.utc)

    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        instance_b = cb.get("/api/v1/hive/identity").json()["instance_id"]

        db = app_a.state.session_factory()
        try:
            db.merge(Setting(key="hive_transfer_window_start", value="0", is_secret=False, updated_at=NOW))
            db.merge(Setting(key="hive_transfer_window_end", value="1440", is_secret=False, updated_at=NOW))
            ruche = db.get(HiveRuche, rid)
            ruche.weekly_quota_bytes = size * 4
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

        db_grant = app_a.state.session_factory()
        try:
            digest = hashlib.sha256(grant.encode()).hexdigest()
            row = db_grant.scalar(
                select(HiveFetchGrant).where(HiveFetchGrant.grant_sha256_hex == digest).limit(1)
            )
            assert row is not None
            grant_id = int(row.id)
        finally:
            db_grant.close()

        assert gate.acquire(grant_id, 2), "slot fantôme : flux précédent pas encore relâché"

        fpath = _file_path(fid)
        base = _grant_headers(rid, grant)
        resp1 = ca.get(fpath, headers=base | {"Range": f"bytes=0-{half - 1}"})
        assert resp1.status_code == 206
        assert len(resp1.content) == half

        resp2 = ca.get(fpath, headers=base | {"Range": f"bytes={half}-{size - 1}"})
        assert resp2.status_code == 206
        assert len(resp2.content) == size - half

        db_q = app_a.state.session_factory()
        try:
            quota = db_q.get(HiveReceiveQuota, (rid, instance_b, ws))
            assert quota is not None
            assert int(quota.bytes_used) == half + (size - half)
        finally:
            db_q.close()

        gate.release(grant_id)
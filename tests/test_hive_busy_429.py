"""429 pair typés : Hive-Busy-Reason et Retry-After."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes
from tests.test_hive_fetch_grant_lot1 import _grant_headers, _mint, _setup_pair
from tests.test_hive_t5 import NOW, URL_A, URL_B, _seed_file_on_a

from mediatracker.db.models import (
    Download,
    HiveFetchGrant,
    HiveReceiveQuota,
    HiveRuche,
    Setting,
)
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.hive_busy import (
    HIVE_BUSY_REASON_HEADER,
    REASON_BANDWIDTH,
    REASON_PARALLEL,
    REASON_QUOTA,
    REASON_RATE,
    REASON_WINDOW,
)
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.hive.transfer_settings import seconds_until_transfer_window_open, seconds_until_week_reset

pytest_plugins = ["tests.test_hive_t5"]


def _headers(resp) -> dict[str, str]:
    return {k.casefold(): v for k, v in resp.headers.items()}


def test_file_get_quota_429_headers(transfer_pair):
    from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests

    app_a, app_b = transfer_pair
    set_peer_asgi_apps_for_tests({URL_A: app_a, URL_B: app_b})
    now = datetime.now(timezone.utc)
    payload = b"q" * 4096
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        instance_b = cb.get("/api/v1/hive/identity").json()["instance_id"]
        db = app_a.state.session_factory()
        try:
            db.merge(Setting(key="hive_transfer_window_start", value="0", is_secret=False, updated_at=NOW))
            db.merge(Setting(key="hive_transfer_window_end", value="1440", is_secret=False, updated_at=NOW))
            ruche = db.get(HiveRuche, rid)
            ruche.weekly_quota_bytes = 50
            from mediatracker.hive.transfer_settings import week_start_date

            ws = week_start_date(now)
            db.merge(
                HiveReceiveQuota(
                    ruche_id=rid,
                    member_instance_id=instance_b,
                    week_start=ws,
                    bytes_used=50,
                )
            )
            db.commit()
        finally:
            db.close()
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        grant = _mint(ca, app_b, rid, fid)
        resp = ca.get(f"/hive/files/{fid}", headers=_grant_headers(rid, grant))
        assert resp.status_code == 429
        hdr = _headers(resp)
        assert hdr.get(HIVE_BUSY_REASON_HEADER.casefold()) == REASON_QUOTA
        assert abs(int(hdr["retry-after"]) - seconds_until_week_reset(now)) <= 2


def test_file_get_window_429_headers(transfer_pair, monkeypatch):
    app_a, app_b = transfer_pair
    closed = NOW.replace(hour=3, minute=0)
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        monkeypatch.setattr("mediatracker.api.hive_router._now", lambda: closed)
        blob = b"R" * catalog_min_logical_video_bytes()
        _, fid, _ = _seed_file_on_a(app_a, rid, blob)
        db = app_a.state.session_factory()
        try:
            db.merge(Setting(key="hive_transfer_window_start", value="0"))
            db.merge(Setting(key="hive_transfer_window_end", value="1"))
            db.commit()
            expected = seconds_until_transfer_window_open(db, closed)
        finally:
            db.close()
        pk_b = load_private_key(app_b.state.settings)
        path = f"/hive/files/{fid}"
        headers = sign_request_headers(
            private_key=pk_b, method="GET", path=path, query="", ruche_id=rid, body=b"", when=closed
        )
        resp = ca.get(path, headers=headers)
        assert resp.status_code == 429
        hdr = _headers(resp)
        assert hdr.get(HIVE_BUSY_REASON_HEADER.casefold()) == REASON_WINDOW
        assert int(hdr["retry-after"]) == expected


def test_file_get_parallel_429_headers(transfer_pair):
    app_a, app_b = transfer_pair
    gate = TransferParallelGate()
    app_a.state.hive_transfer_parallel = gate
    payload = b"p" * catalog_min_logical_video_bytes()
    with TestClient(app_a) as ca, TestClient(app_b) as cb:
        rid = _setup_pair(app_a, app_b, ca, cb)
        db = app_a.state.session_factory()
        try:
            db.merge(Setting(key="hive_transfer_parallel", value="1", is_secret=False, updated_at=NOW))
            db.commit()
        finally:
            db.close()
        _, fid, _ = _seed_file_on_a(app_a, rid, payload)
        _mint(ca, app_b, rid, fid)
        g2 = _mint(ca, app_b, rid, fid)
        db2 = app_a.state.session_factory()
        try:
            row = db2.scalars(select(HiveFetchGrant).order_by(HiveFetchGrant.id)).first()
        finally:
            db2.close()
        gate.acquire(int(row.id), 1)
        resp = ca.get(f"/hive/files/{fid}", headers=_grant_headers(rid, g2))
        assert resp.status_code == 429
        assert _headers(resp).get(HIVE_BUSY_REASON_HEADER.casefold()) == REASON_PARALLEL


def test_peer_429_bandwidth_header_shape():
    from mediatracker.api.hive_router import _peer_429
    from mediatracker.hive.hive_busy import DEFAULT_RETRY_BANDWIDTH

    resp = _peer_429(REASON_BANDWIDTH, DEFAULT_RETRY_BANDWIDTH)
    hdr = _headers(resp)
    assert hdr.get(HIVE_BUSY_REASON_HEADER.casefold()) == REASON_BANDWIDTH


def test_peer_429_rate_header_shape():
    from mediatracker.api.hive_router import _peer_429
    from mediatracker.hive.hive_busy import DEFAULT_RETRY_RATE

    resp = _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
    assert resp.status_code == 429
    hdr = _headers(resp)
    assert hdr.get(HIVE_BUSY_REASON_HEADER.casefold()) == REASON_RATE
    assert int(hdr["retry-after"]) == 60


def test_defer_remote_quota_sets_user_message():
    from mediatracker.hive.download_pipeline import defer_hive_peer_busy_from_remote

    now = datetime.now(timezone.utc)
    row = Download(id=1, media_item_id=1, state="downloading")
    defer_hive_peer_busy_from_remote(
        row,
        now,
        busy_reason=REASON_QUOTA,
        retry_after_seconds=86_400,
        peer_instance_id="aa",
    )
    assert row.state == "pending"
    assert row.error_code == "HIVE_PEER_BUSY"
    assert "Quota hebdomadaire atteint" in (row.error or "")
    assert (row.next_retry_at - now).total_seconds() > 3600
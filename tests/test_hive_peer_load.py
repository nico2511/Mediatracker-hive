"""Instantané charge pair : validation, gossip, classement, API."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient
from tests.conftest import csrf, login
from tests.test_hive_t3_peer_http import _enable, _settings_pair
from tests.test_hive_t5 import URL_A, URL_B, _join_existing_ruche

from mediatracker.api.app import create_app
from mediatracker.db.models import HivePeerLoad, HiveRuche, Setting
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.hive.transfer_load import (
    LOAD_MAX_FUTURE_SECONDS,
    LOAD_STALE_SECONDS,
    bind_transfer_load_runtime,
    build_local_load_snapshot,
    ingest_peer_load,
    load_is_stale,
    peer_load_row,
    validate_load_payload,
)
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.hive.transfer_sources import rank_sources

NOW = datetime(2099, 6, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def transfer_pair(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "load_a"))
    app_b = create_app(_settings_pair(tmp_path, "load_b"))
    for app in (app_a, app_b):
        app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        app.state.hive_send_bandwidth = TransferBandwidthLimiter(bps=0)
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()
        set_peer_asgi_apps_for_tests(None)


def _valid_load(
    *, busy: bool = False, out_active: int = 0, parallel_limit: int = 2, at: datetime | None = None
) -> dict:
    moment = at or NOW
    return {
        "out_active": out_active,
        "parallel_limit": parallel_limit,
        "send_cap_bps": 0,
        "send_avail_bps": None,
        "busy": busy,
        "at": moment.isoformat().replace("+00:00", "Z"),
    }


def _valid_load_live(**kwargs) -> dict:
    return _valid_load(at=datetime.now(timezone.utc), **kwargs)


def test_validate_load_payload_bounds():
    assert validate_load_payload(_valid_load()) is not None
    assert validate_load_payload(None) is None
    assert validate_load_payload({}) is None
    bad = _valid_load()
    bad["at"] = "not-a-date"
    assert validate_load_payload(bad) is None
    bad2 = _valid_load()
    bad2["busy"] = "yes"
    assert validate_load_payload(bad2) is None


def test_validate_load_payload_missing_field_ignored_compat():
    partial = {"out_active": 0}
    assert validate_load_payload(partial) is None


def test_load_stale_and_ingest(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x09" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.commit()
        assert ingest_peer_load(db, ruche_id=rid, origin_instance_id="peer-a", load=_valid_load(), now=NOW)
        db.commit()
        fresh = peer_load_row(db, ruche_id=rid, origin_instance_id="peer-a", now=NOW)
        assert fresh is not None and not fresh.stale
        old = NOW - timedelta(seconds=LOAD_STALE_SECONDS + 1)
        assert load_is_stale(old, NOW)
        stale_moment = NOW + timedelta(seconds=LOAD_STALE_SECONDS + 5)
        view = peer_load_row(db, ruche_id=rid, origin_instance_id="peer-a", now=stale_moment)
        assert view is not None and view.stale
    finally:
        db.close()


def test_build_local_load_snapshot_none_without_gate(transfer_pair):
    bind_transfer_load_runtime(parallel_gate=None, send_bandwidth=None)
    app_a, _ = transfer_pair
    db = app_a.state.session_factory()
    try:
        assert build_local_load_snapshot(db, NOW) is None
    finally:
        db.close()


def test_worker_gossip_omits_load_without_gate(transfer_pair, monkeypatch):
    bind_transfer_load_runtime(parallel_gate=None, send_bandwidth=None)
    app_a, _ = transfer_pair
    with TestClient(app_a) as client:
        _enable(client, "aa", URL_A)
    captured: list[object] = []

    def _fake_sync(*, load=None, **kwargs):
        del kwargs
        captured.append(load)

        class _Resp:
            status_code = 404

            def json(self):
                return {}

        return _Resp()

    monkeypatch.setattr("mediatracker.hive.gossip_exchange.gossip_sync_once", _fake_sync)
    from mediatracker.hive.gossip_exchange import pull_gossip_from_peer

    db = app_a.state.session_factory()
    try:
        pull_gossip_from_peer(
            db,
            app_a.state.settings,
            ruche_id=b"\x11" * 16,
            peer_base_url=URL_B,
            now=NOW,
        )
        assert captured and captured[0] is None
    finally:
        db.close()


def test_build_local_load_snapshot_no_secrets(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    gate = app_a.state.hive_transfer_parallel
    try:
        snap = build_local_load_snapshot(db, NOW, parallel_gate=gate)
        assert snap is not None
        text = json.dumps(snap)
        assert "tmdb" not in text.lower()
        assert "path" not in text.lower()
        assert {"out_active", "parallel_limit", "send_cap_bps", "busy", "at"} <= set(snap)
    finally:
        db.close()


def test_ingest_rejects_regression_and_far_future(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x12" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.commit()
        newer = _valid_load(out_active=2)
        newer_at = NOW + timedelta(seconds=10)
        newer["at"] = newer_at.isoformat().replace("+00:00", "Z")
        assert ingest_peer_load(db, ruche_id=rid, origin_instance_id="p1", load=newer, now=NOW)
        db.commit()
        older = _valid_load(out_active=0)
        older["at"] = NOW.isoformat().replace("+00:00", "Z")
        assert not ingest_peer_load(db, ruche_id=rid, origin_instance_id="p1", load=older, now=NOW)
        db.commit()
        row = db.get(HivePeerLoad, (rid, "p1"))
        assert row is not None and row.out_active == 2
        future = _valid_load()
        future["at"] = (NOW + timedelta(seconds=LOAD_MAX_FUTURE_SECONDS + 30)).isoformat().replace("+00:00", "Z")
        assert not ingest_peer_load(db, ruche_id=rid, origin_instance_id="p1", load=future, now=NOW)
    finally:
        db.close()


def test_send_avail_bps_requires_observed_outbound(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    gate = TransferParallelGate()
    lim_unmetered = TransferBandwidthLimiter(bps=0)
    lim_metered = TransferBandwidthLimiter(bps=1_000_000)
    try:
        row = db.get(Setting, "hive_transfer_send_bps")
        if row is None:
            db.add(Setting(key="hive_transfer_send_bps", value="1000000"))
        else:
            row.value = "1000000"
        db.commit()
        snap_no_meter = build_local_load_snapshot(db, NOW, parallel_gate=gate, send_bandwidth=lim_unmetered)
        assert snap_no_meter is not None
        assert snap_no_meter["send_avail_bps"] is None
        t = {"now": 100.0}
        lim_metered.set_clock(lambda: t["now"])
        lim_metered.reserve("peer-x", 900_000)
        t["now"] = 100.5
        lim_metered.reserve("peer-x", 900_000)
        snap_metered = build_local_load_snapshot(db, NOW, parallel_gate=gate, send_bandwidth=lim_metered)
        assert snap_metered is not None
        assert snap_metered["send_avail_bps"] is not None
        assert snap_metered["send_avail_bps"] < 1_000_000
    finally:
        db.close()


def test_rank_sources_busy_last_and_free_capacity(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x0a" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.flush()
        db.add(
            HivePeerLoad(
                ruche_id=rid,
                origin_instance_id="busy",
                out_active=2,
                parallel_limit=2,
                send_cap_bps=0,
                send_avail_bps=None,
                busy=True,
                snapshot_at=NOW,
                received_at=NOW,
            )
        )
        db.add(
            HivePeerLoad(
                ruche_id=rid,
                origin_instance_id="free",
                out_active=0,
                parallel_limit=4,
                send_cap_bps=0,
                send_avail_bps=None,
                busy=False,
                snapshot_at=NOW,
                received_at=NOW,
            )
        )
        db.commit()
        sources = [
            {"ruche_id_hex": rid.hex(), "origine": "busy", "file_id_hex": "aa" * 16, "sha256": "c" * 64},
            {"ruche_id_hex": rid.hex(), "origine": "free", "file_id_hex": "bb" * 16, "sha256": "c" * 64},
            {"ruche_id_hex": rid.hex(), "origine": "unknown", "file_id_hex": "cc" * 16, "sha256": "c" * 64},
        ]
        ordered = rank_sources(db, sources, now=NOW, rng=random.Random(0))
        assert ordered[0]["origine"] == "free"
        assert ordered[-1]["origine"] == "busy"
        mid = [s["origine"] for s in ordered[1:-1]]
        assert "unknown" in mid
    finally:
        db.close()


def test_gossip_sync_load_roundtrip(transfer_pair):
    app_a, app_b = transfer_pair
    from mediatracker.hive.crypto_id import hive_public_id_from_public_key
    from mediatracker.hive.peer_client import gossip_sync
    from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", URL_A)
        _enable(client_b, "bb", URL_B)
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        ruche_id = _join_existing_ruche(client_a, client_b, rid, invitee_url=URL_B)
        pk_b = load_private_key(client_b.app.state.settings)
        assert pk_b is not None
        origin_b = hive_public_id_from_public_key(pk_b.public_key())
        set_peer_asgi_app_for_tests(client_a.app)
        resp = gossip_sync(
            base_url=URL_A,
            ruche_id=ruche_id,
            private_key=pk_b,
            vector={},
            load=_valid_load_live(out_active=1),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "load" in data
        assert isinstance(data["load"], dict)
        db = client_a.app.state.session_factory()
        try:
            row_b = db.get(HivePeerLoad, (ruche_id, origin_b))
            assert row_b is not None
            assert row_b.out_active == 1
        finally:
            db.close()


def test_gossip_sync_without_load_field(transfer_pair):
    app_a, app_b = transfer_pair
    from mediatracker.hive.peer_client import gossip_sync
    from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", URL_A)
        _enable(client_b, "bb", URL_B)
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        ruche_id = _join_existing_ruche(client_a, client_b, rid, invitee_url=URL_B)
        pk_b = load_private_key(client_b.app.state.settings)
        assert pk_b is not None
        set_peer_asgi_app_for_tests(client_a.app)
        resp = gossip_sync(base_url=URL_A, ruche_id=ruche_id, private_key=pk_b, vector={})
        assert resp.status_code == 200
        assert resp.json().get("load") is not None


def test_api_peers_load(transfer_pair):
    app_a, _ = transfer_pair
    with TestClient(app_a) as client:
        _enable(client, "aa", URL_A)
        login(client)
        resp = client.get("/api/v1/hive/peers/load", headers=csrf(client))
        assert resp.status_code == 200
        payload = resp.json()
        assert "items" in payload
        assert isinstance(payload["items"], list)
        assert "self" in payload
        self_row = payload["self"]
        assert self_row is not None
        assert isinstance(self_row["instance_id"], str)
        assert self_row["parallel_limit"] >= 1
        assert "out_active" in self_row
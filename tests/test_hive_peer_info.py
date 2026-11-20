"""Instantané version pair : validation, gossip, détail ruche."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient
from tests.conftest import TEST_ADMIN_PASSWORD as _TEST_ADMIN_PASSWORD
from tests.conftest import csrf, login
from tests.test_hive_t3_peer_http import _enable
from tests.test_hive_t5 import URL_A, URL_B, _join_existing_ruche

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.db.models import HivePeerInfo, HiveRuche
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_client import gossip_sync
from mediatracker.hive.peer_info import (
    HIVE_PROTOCOL,
    build_local_instance_snapshot,
    ingest_peer_info,
    peer_info_row,
    peer_protocol,
    peer_supports,
    validate_instance_payload,
)
from mediatracker.hive.peer_rate_limit import PeerRateLimiter
from mediatracker.hive.peer_transport import set_peer_asgi_apps_for_tests
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.hive.transfer_load import LOAD_MAX_FUTURE_SECONDS, bind_transfer_load_runtime


def _settings_pair(tmp_path, suffix: str) -> Settings:
    base = tmp_path / suffix
    base.mkdir(parents=True, exist_ok=True)
    return Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password=_TEST_ADMIN_PASSWORD,
        database_url=f"sqlite:///{base / 'app.db'}",
        data_dir=base / "data",
        config_dir=base / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
    )


NOW = datetime(2099, 6, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def peer_info_pair(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "info_a"))
    app_b = create_app(_settings_pair(tmp_path, "info_b"))
    for app in (app_a, app_b):
        app.state.hive_peer_limiter = PeerRateLimiter(limit=10_000, window_seconds=60.0)
        app.state.hive_send_bandwidth = TransferBandwidthLimiter(bps=0)
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()
        set_peer_asgi_apps_for_tests(None)


def _valid_instance(*, at: datetime | None = None, protocol: int = HIVE_PROTOCOL) -> dict:
    moment = at or NOW
    return {
        "version": "9.9.9-test",
        "hive_protocol": protocol,
        "capabilities": [],
        "at": moment.isoformat().replace("+00:00", "Z"),
    }


def test_build_local_instance_snapshot_always_emitted(peer_info_pair):
    bind_transfer_load_runtime(parallel_gate=None, send_bandwidth=None)
    snap = build_local_instance_snapshot(NOW)
    assert isinstance(snap, dict)
    assert "version" in snap
    assert snap["hive_protocol"] == HIVE_PROTOCOL
    assert snap["capabilities"] == ["catalog_epoch"]
    assert "at" in snap


def test_build_local_instance_snapshot_no_secrets(peer_info_pair):
    snap = build_local_instance_snapshot(NOW)
    text = json.dumps(snap).lower()
    assert "secret" not in text
    assert "://" not in text
    assert "password" not in text


def test_validate_instance_bounds_and_allowlist(peer_info_pair):
    assert validate_instance_payload(_valid_instance()) is not None
    bad = _valid_instance()
    bad["version"] = "x" * 65
    assert validate_instance_payload(bad) is None
    bad2 = _valid_instance()
    bad2["hive_protocol"] = 70000
    assert validate_instance_payload(bad2) is None
    bad3 = _valid_instance()
    bad3["capabilities"] = ["unknown_cap"]
    normalized = validate_instance_payload(bad3)
    assert normalized is not None
    assert normalized["capabilities"] == []
    bad4 = _valid_instance()
    bad4["capabilities"] = ["Bad-Cap"]
    assert validate_instance_payload(bad4) is None


def test_ingest_at_and_regression(peer_info_pair):
    app_a, _ = peer_info_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x21" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.commit()
        assert ingest_peer_info(
            db, ruche_id=rid, origin_instance_id="p9", instance=_valid_instance(), now=NOW
        )
        db.commit()
        newer = _valid_instance(protocol=2)
        newer["at"] = (NOW + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
        assert ingest_peer_info(db, ruche_id=rid, origin_instance_id="p9", instance=newer, now=NOW)
        db.commit()
        older = _valid_instance(protocol=1)
        assert not ingest_peer_info(db, ruche_id=rid, origin_instance_id="p9", instance=older, now=NOW)
        row = db.get(HivePeerInfo, (rid, "p9"))
        assert row is not None and row.hive_protocol == 2
        future = _valid_instance()
        future["at"] = (NOW + timedelta(seconds=LOAD_MAX_FUTURE_SECONDS + 30)).isoformat().replace(
            "+00:00", "Z"
        )
        assert not ingest_peer_info(db, ruche_id=rid, origin_instance_id="p9", instance=future, now=NOW)
    finally:
        db.close()


def test_peer_without_instance_field_sync_200(peer_info_pair):
    app_a, app_b = peer_info_pair
    from mediatracker.hive.crypto_id import hive_public_id_from_public_key
    from mediatracker.hive.peer_client import gossip_sync as gs

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", URL_A)
        _enable(client_b, "bb", URL_B)
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        ruche_id = _join_existing_ruche(client_a, client_b, rid, invitee_url=URL_B)
        pk_b = load_private_key(client_b.app.state.settings)
        assert pk_b is not None
        origin_b = hive_public_id_from_public_key(pk_b.public_key())
        set_peer_asgi_apps_for_tests({URL_A: client_a.app})
        resp = gs(base_url=URL_A, ruche_id=ruche_id, private_key=pk_b, vector={})
        assert resp.status_code == 200
        assert isinstance(resp.json().get("instance"), dict)
        db = client_a.app.state.session_factory()
        try:
            assert peer_protocol(db, ruche_id=ruche_id, origin_instance_id=origin_b) == 0
        finally:
            db.close()


def test_gossip_sync_instance_roundtrip(peer_info_pair):
    app_a, app_b = peer_info_pair
    from mediatracker.hive.crypto_id import hive_public_id_from_public_key

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", URL_A)
        _enable(client_b, "bb", URL_B)
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        ruche_id = _join_existing_ruche(client_a, client_b, rid, invitee_url=URL_B)
        pk_b = load_private_key(client_b.app.state.settings)
        assert pk_b is not None
        origin_b = hive_public_id_from_public_key(pk_b.public_key())
        set_peer_asgi_apps_for_tests({URL_A: client_a.app})
        instance = build_local_instance_snapshot(datetime.now(timezone.utc))
        resp = gossip_sync(
            base_url=URL_A,
            ruche_id=ruche_id,
            private_key=pk_b,
            vector={},
            instance=instance,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("instance"), dict)
        db = client_a.app.state.session_factory()
        try:
            row = peer_info_row(db, ruche_id=ruche_id, origin_instance_id=origin_b)
            assert row is not None
            assert row.hive_protocol == instance["hive_protocol"]
        finally:
            db.close()


def test_worker_gossip_emits_instance_without_gate(peer_info_pair, monkeypatch):
    bind_transfer_load_runtime(parallel_gate=None, send_bandwidth=None)
    app_a, _ = peer_info_pair
    with TestClient(app_a) as client:
        _enable(client, "aa", URL_A)
    captured: list[object] = []

    def _fake_sync(*, instance=None, **kwargs):
        del kwargs
        captured.append(instance)

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
            ruche_id=b"\x31" * 16,
            peer_base_url=URL_B,
            now=NOW,
        )
        assert captured and isinstance(captured[0], dict)
    finally:
        db.close()


def test_ruche_detail_self_and_peer(peer_info_pair):
    app_a, app_b = peer_info_pair
    from mediatracker.hive.crypto_id import hive_public_id_from_public_key

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", URL_A)
        _enable(client_b, "bb", URL_B)
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        ruche_id = _join_existing_ruche(client_a, client_b, rid, invitee_url=URL_B)
        pk_b = load_private_key(client_b.app.state.settings)
        assert pk_b is not None
        origin_b = hive_public_id_from_public_key(pk_b.public_key())
        set_peer_asgi_apps_for_tests({URL_A: client_a.app})
        old_peer = _valid_instance(protocol=0, at=datetime.now(timezone.utc))
        resp = gossip_sync(
            base_url=URL_A,
            ruche_id=ruche_id,
            private_key=pk_b,
            vector={},
            instance=old_peer,
        )
        assert resp.status_code == 200
        login(client_a)
        detail = client_a.get(f"/api/v1/hive/ruches/{rid}/detail", headers=csrf(client_a)).json()
        self_row = next(m for m in detail["members"] if m["is_self"])
        peer_row = next(m for m in detail["members"] if m["instance_id"] == origin_b)
        assert self_row["hive_protocol"] == HIVE_PROTOCOL
        assert self_row["app_version"]
        assert peer_row["app_version"] == old_peer["version"]
        assert peer_row["hive_protocol"] == 0


def test_peer_supports_inactive_false(peer_info_pair):
    app_a, _ = peer_info_pair
    with TestClient(app_a):
        pass
    db = app_a.state.session_factory()
    rid = b"\x22" * 16
    try:
        db.add(HiveRuche(ruche_id=rid))
        db.commit()
        assert not peer_supports(db, ruche_id=rid, origin_instance_id="ghost", capability="x")
        assert peer_protocol(db, ruche_id=rid, origin_instance_id="ghost") == 0
    finally:
        db.close()

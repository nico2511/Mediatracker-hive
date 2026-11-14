"""Hive T3 : adhésion HTTP pair et 401 après exclusion (deux apps, deux DB)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.db.models import HiveEntry, HiveMember
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.peer_client import signed_peer_request
from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests

NOW = datetime(2026, 11, 6, 16, 0, tzinfo=timezone.utc)
CREATOR_URL = "https://mtr.exemple.fr"


def _settings_pair(tmp_path, suffix: str) -> Settings:
    base = tmp_path / suffix
    base.mkdir(parents=True, exist_ok=True)
    return Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="test-admin-password",
        database_url=f"sqlite:///{base / 'app.db'}",
        data_dir=base / "data",
        config_dir=base / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
    )


def _enable(client, name: str, public_address: str):
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": name,
                "hive_public_address": public_address,
            }
        },
        headers=csrf(client),
    )
    assert r.status_code == 200, r.text


def _create_invitation(client, ruche_id_hex: str) -> str:
    login(client)
    r = client.post(
        "/api/v1/hive/ruches/invitations",
        json={"ruche_id_hex": ruche_id_hex},
        headers=csrf(client),
    )
    assert r.status_code == 201, r.text
    return r.json()["link"]


@pytest.fixture
def hive_apps(tmp_path):
    app_a = create_app(_settings_pair(tmp_path, "inst_a"))
    app_b = create_app(_settings_pair(tmp_path, "inst_b"))
    try:
        yield app_a, app_b
    finally:
        app_a.state.engine.dispose()
        app_b.state.engine.dispose()
        set_peer_asgi_app_for_tests(None)


def test_http_join_two_instances(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        creator_id = client_a.get("/api/v1/hive/identity").json()["instance_id"]

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        begun = client_b.post(
            "/api/v1/hive/join/begin",
            json={"link": link, "invitee_name": "bb", "invitee_public_address": ""},
            headers=csrf(client_b),
        )
        assert begun.status_code == 200, begun.text
        body = begun.json()
        assert len(body["control_code"]) == 8
        pending_id = body["pending_id"]

        login(client_a)
        assert client_a.post(
            "/api/v1/hive/join/accept", json={"pending_id": pending_id}, headers=csrf(client_a)
        ).status_code == 204

        confirmed = client_b.post(
            "/api/v1/hive/join/confirm",
            json={
                "ruche_id_hex": rid,
                "creator_base_url": CREATOR_URL,
                "creator_instance_id": creator_id,
            },
            headers=csrf(client_b),
        )
        assert confirmed.status_code == 200, confirmed.text

        db_b = app_b.state.session_factory()
        invitee_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        member = db_b.scalar(
            select(HiveMember).where(
                HiveMember.ruche_id == bytes.fromhex(rid), HiveMember.member_instance_id == invitee_id
            )
        )
        assert member is not None
        db_b.close()


def test_excluded_member_401_and_catalog_isolation(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "xx", "https://mtr-test.exemple.fr")
        login(client_a)
        rid_a = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        rid_b = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        creator_id = client_a.get("/api/v1/hive/identity").json()["instance_id"]
        set_peer_asgi_app_for_tests(app_a)

        link_a = _create_invitation(client_a, rid_a)
        link_b = _create_invitation(client_a, rid_b)
        for link, rid_hex in ((link_a, rid_a), (link_b, rid_b)):
            login(client_b)
            begun = client_b.post(
                "/api/v1/hive/join/begin",
                json={"link": link, "invitee_name": "xx", "invitee_public_address": "https://mtr-test.exemple.fr"},
                headers=csrf(client_b),
            )
            assert begun.status_code == 200
            pending_id = begun.json()["pending_id"]
            login(client_a)
            client_a.post("/api/v1/hive/join/accept", json={"pending_id": pending_id}, headers=csrf(client_a))
            r = client_b.post(
                "/api/v1/hive/join/confirm",
                json={
                    "ruche_id_hex": rid_hex,
                    "creator_base_url": CREATOR_URL,
                    "creator_instance_id": creator_id,
                },
                headers=csrf(client_b),
            )
            assert r.status_code == 200, r.text

        invitee_id = client_b.get("/api/v1/hive/identity").json()["instance_id"]
        db_a = app_a.state.session_factory()
        for rid_hex in (rid_a, rid_b):
            db_a.add(
                HiveEntry(
                    ruche_id=bytes.fromhex(rid_hex),
                    origin_instance_id=invitee_id,
                    seq=1,
                    etat="disponible",
                    sha256_hex="b" * 64,
                    payload_json="{}",
                    signature_b64="e30=",
                )
            )
        db_a.commit()

        pk_b = load_private_key(app_b.state.settings)
        assert pk_b is not None
        for rid_hex in (rid_a, rid_b):
            resp = signed_peer_request(
                base_url=CREATOR_URL,
                method="GET",
                path=f"/hive/ruche/{rid_hex}/access-check",
                ruche_id=bytes.fromhex(rid_hex),
                private_key=pk_b,
            )
            assert resp.status_code == 204

        login(client_a)
        assert client_a.post(
            f"/api/v1/hive/ruches/{rid_a}/exclude",
            json={"instance_id": invitee_id},
            headers=csrf(client_a),
        ).status_code == 204

        assert (
            signed_peer_request(
                base_url=CREATOR_URL,
                method="GET",
                path=f"/hive/ruche/{rid_a}/access-check",
                ruche_id=bytes.fromhex(rid_a),
                private_key=pk_b,
            ).status_code
            == 401
        )
        assert (
            signed_peer_request(
                base_url=CREATOR_URL,
                method="GET",
                path=f"/hive/ruche/{rid_b}/access-check",
                ruche_id=bytes.fromhex(rid_b),
                private_key=pk_b,
            ).status_code
            == 204
        )

        rows_a = list(
            db_a.scalars(
                select(HiveEntry).where(
                    HiveEntry.ruche_id == bytes.fromhex(rid_a),
                    HiveEntry.origin_instance_id == invitee_id,
                )
            ).all()
        )
        rows_b = list(
            db_a.scalars(
                select(HiveEntry).where(
                    HiveEntry.ruche_id == bytes.fromhex(rid_b),
                    HiveEntry.origin_instance_id == invitee_id,
                )
            ).all()
        )
        assert all(r.etat == "retire" for r in rows_a)
        assert all(r.etat == "disponible" for r in rows_b)
        db_a.close()

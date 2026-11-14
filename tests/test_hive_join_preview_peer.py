"""Preview invitation sur instance invitée (DB sans jeton, validation chez le créateur)."""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_hive_t3_peer_http import CREATOR_URL, _create_invitation, _enable, _settings_pair

from mediatracker.api.app import create_app
from mediatracker.db.models import HiveInvitation
from mediatracker.hive.crypto_id import generate_keypair, public_key_to_pem
from mediatracker.hive.invitations import (
    create_invitation,
    decode_invitation_link,
    encode_invitation_link,
    revoke_invitation,
)
from mediatracker.hive.membership import local_instance_identity
from mediatracker.hive.peer_transport import set_peer_asgi_app_for_tests


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


def test_preview_join_two_instances(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": link},
            headers=csrf(client_b),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ruche_id_hex"] == rid
        assert body["member_count"] == 1
        assert "expires_at" in body

        db_a = app_a.state.session_factory()
        consumed = list(db_a.scalars(select(HiveInvitation)).all())
        assert all(row.consumed_at is None for row in consumed)
        db_a.close()

        db_b = app_b.state.session_factory()
        assert list(db_b.scalars(select(HiveInvitation)).all()) == []
        db_b.close()


def test_creator_lists_pending_after_begin(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        begun = client_b.post(
            "/api/v1/hive/join/begin",
            json={"link": link, "invitee_name": "bb", "invitee_public_address": ""},
            headers=csrf(client_b),
        )
        assert begun.status_code == 200, begun.text
        control = begun.json()["control_code"]
        login(client_a)
        pending = client_a.get(f"/api/v1/hive/join/pending/{rid}", headers=csrf(client_a))
        assert pending.status_code == 200, pending.text
        body = pending.json()
        assert len(body) == 1
        assert body[0]["invitee_name"] == "bb"
        assert body[0]["control_code"] == control


def test_preview_does_not_block_begin(hive_apps):
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
        assert (
            client_b.post(
                "/api/v1/hive/join/preview",
                json={"link": link},
                headers=csrf(client_b),
            ).status_code
            == 200
        )
        begun = client_b.post(
            "/api/v1/hive/join/begin",
            json={"link": link, "invitee_name": "bb", "invitee_public_address": ""},
            headers=csrf(client_b),
        )
        assert begun.status_code == 200, begun.text
        pending_id = begun.json()["pending_id"]
        login(client_a)
        assert client_a.post(
            "/api/v1/hive/join/accept", json={"pending_id": pending_id}, headers=csrf(client_a)
        ).status_code == 204
        assert (
            client_b.post(
                "/api/v1/hive/join/confirm",
                json={
                    "ruche_id_hex": rid,
                    "creator_base_url": CREATOR_URL,
                    "creator_instance_id": creator_id,
                },
                headers=csrf(client_b),
            ).status_code
            == 200
        )
        login(client_a)
        members_a = client_a.get(f"/api/v1/hive/ruches/{rid}/detail", headers=csrf(client_a)).json()["members"]
        assert any(m.get("display_name") == "bb" for m in members_a)
        login(client_b)
        ruches_b = client_b.get("/api/v1/hive/ruches", headers=csrf(client_b)).json()
        assert any(r["ruche_id_hex"] == rid and r["member_count"] >= 2 for r in ruches_b)


def test_preview_rejects_bad_link_on_peer(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        payload, sig = decode_invitation_link(link)
        payload["token_b64"] = "AAAAAAAAAAAAAAAAAAAAAA=="
        from mediatracker.hive.invitations import encode_invitation_link

        bad = encode_invitation_link(payload, sig)

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": bad},
            headers=csrf(client_b),
        )
        assert r.status_code == 422
        assert "unknown_token" in r.text.lower() or "token" in r.text.lower()


def test_preview_rejects_expired_invitation(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)

        db_a = app_a.state.session_factory()
        inv = db_a.scalar(select(HiveInvitation).limit(1))
        inv.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        db_a.commit()
        db_a.close()

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": link},
            headers=csrf(client_b),
        )
        assert r.status_code == 422
        assert "expired" in r.text.lower()


def _link_with_loopback_creator(client, app, settings, rid_hex: str) -> str:
    login(client)
    db = app.state.session_factory()
    pk, creator_id, _, _, pem, address = local_instance_identity(db, settings)
    _, link, _ = create_invitation(
        db,
        ruche_id=bytes.fromhex(rid_hex),
        creator_id=creator_id,
        creator_address="http://127.0.0.1:8080",
        creator_private_key=pk,
        creator_public_key_pem=pem,
        now=datetime.now(timezone.utc),
    )
    db.commit()
    db.close()
    assert address  # adresse publique créateur légitime, distincte du lien falsifié
    return link


def test_preview_rejects_blocked_creator_destination(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _link_with_loopback_creator(client_a, app_a, app_a.state.settings, rid)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": link},
            headers=csrf(client_b),
        )
        assert r.status_code == 422
        assert "peer_destination_rejected" in r.text


def test_preview_rejects_altered_signature_valid_token(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        payload, _sig = decode_invitation_link(link)
        bogus_sig = base64.b64encode(b"\x00" * 64).decode("ascii")
        tampered = encode_invitation_link(payload, bogus_sig)
        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": tampered},
            headers=csrf(client_b),
        )
        assert r.status_code == 422
        assert "invalid_signature" in r.text


def test_preview_rejects_creator_identity_mismatch(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        _, foreign_pub = generate_keypair()
        foreign_pem = public_key_to_pem(foreign_pub).decode("utf-8")

        def fake_peer(**_kwargs):
            return httpx.Response(
                200,
                json={
                    "ruche_id_hex": rid,
                    "member_count": 1,
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "creator_public_key_pem": foreign_pem,
                },
            )

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        with patch("mediatracker.hive.peer_membership.signed_peer_request", side_effect=fake_peer):
            r = client_b.post(
                "/api/v1/hive/join/preview",
                json={"link": link},
                headers=csrf(client_b),
            )
        assert r.status_code == 422
        assert "creator_identity_mismatch" in r.text


def test_preview_rejects_ruche_mismatch(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        db_a = app_a.state.session_factory()
        pem = local_instance_identity(db_a, app_a.state.settings)[4]
        db_a.close()

        def fake_peer(**_kwargs):
            return httpx.Response(
                200,
                json={
                    "ruche_id_hex": "0" * 32,
                    "member_count": 1,
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "creator_public_key_pem": pem,
                },
            )

        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        with patch("mediatracker.hive.peer_membership.signed_peer_request", side_effect=fake_peer):
            r = client_b.post(
                "/api/v1/hive/join/preview",
                json={"link": link},
                headers=csrf(client_b),
            )
        assert r.status_code == 422
        assert "ruche_mismatch" in r.text


def test_preview_rejects_revoked_on_creator(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        db_a = app_a.state.session_factory()
        inv = db_a.scalar(select(HiveInvitation).limit(1))
        creator_id = local_instance_identity(db_a, app_a.state.settings)[1]
        revoke_invitation(db_a, invitation_id=inv.id, creator_id=creator_id, now=datetime.now(timezone.utc))
        db_a.commit()
        db_a.close()
        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": link},
            headers=csrf(client_b),
        )
        assert r.status_code == 422
        assert "revoked" in r.text.lower()


def test_preview_rejects_consumed_on_creator(hive_apps):
    app_a, app_b = hive_apps
    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        _enable(client_a, "aa", CREATOR_URL)
        _enable(client_b, "bb", "")
        login(client_a)
        rid = client_a.post("/api/v1/hive/ruches", headers=csrf(client_a)).json()["ruche_id_hex"]
        link = _create_invitation(client_a, rid)
        db_a = app_a.state.session_factory()
        inv = db_a.scalar(select(HiveInvitation).limit(1))
        inv.consumed_at = datetime.now(timezone.utc)
        db_a.commit()
        db_a.close()
        set_peer_asgi_app_for_tests(app_a)
        login(client_b)
        r = client_b.post(
            "/api/v1/hive/join/preview",
            json={"link": link},
            headers=csrf(client_b),
        )
        assert r.status_code == 422
        assert "consumed" in r.text.lower()

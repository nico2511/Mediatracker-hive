"""Hive T1 : interrupteur, identité, /hive/ sous contrôle."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.conftest import csrf, login

from mediatracker.hive.crypto_id import generate_keypair, private_key_from_pem
from mediatracker.hive.identity import hive_data_dir, hive_private_key_path, write_private_key


def test_hive_path_404_when_disabled(client):
    assert client.get("/hive/any").status_code == 404


def test_hive_private_key_file_mode(settings):
    app = __import__("mediatracker.api.app", fromlist=["create_app"]).create_app(settings)
    try:
        with TestClient(app) as tc:
            login(tc)
            r = tc.patch(
                "/api/v1/settings",
                json={
                    "values": {
                        "hive_enabled": True,
                        "hive_activation_name": "ab",
                        "hive_public_address": "",
                    }
                },
                headers=csrf(tc),
            )
            assert r.status_code == 200, r.text
            path = hive_private_key_path(settings)
            assert path.is_file()
            mode = oct(path.stat().st_mode)[-3:]
            assert mode == "600"
            parent = hive_data_dir(settings)
            assert oct(parent.stat().st_mode)[-3:] == "700"
            pem = path.read_bytes()
            private_key_from_pem(pem)
    finally:
        app.state.engine.dispose()


def test_hive_private_key_atomic_create_no_insecure_window(settings, monkeypatch):
    """Fichier temporaire créé avec O_CREAT|O_EXCL|0o600 puis os.replace vers identity.key."""
    tmp_created = []
    original_open = os.open
    original_replace = os.replace

    def tracking_open(path, flags, mode=0o777, *args, **kwargs):
        p = str(path)
        if ".tmp" in Path(p).name and Path(p).parent == hive_data_dir(settings):
            tmp_created.append((p, flags, mode))
            assert flags & os.O_CREAT
            assert flags & os.O_EXCL
            assert mode == 0o600
        return original_open(path, flags, mode, *args, **kwargs)

    replaces: list[tuple[str, str]] = []

    def tracking_replace(src, dst, *args, **kwargs):
        replaces.append((str(src), str(dst)))
        return original_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "open", tracking_open)
    monkeypatch.setattr(os, "replace", tracking_replace)

    app = __import__("mediatracker.api.app", fromlist=["create_app"]).create_app(settings)
    try:
        with TestClient(app) as tc:
            login(tc)
            r = tc.patch(
                "/api/v1/settings",
                json={
                    "values": {
                        "hive_enabled": True,
                        "hive_activation_name": "ab",
                        "hive_public_address": "",
                    }
                },
                headers=csrf(tc),
            )
            assert r.status_code == 200, r.text
        final = hive_private_key_path(settings)
        assert tmp_created
        assert any(str(final) == d for _, d in replaces)
        assert final.is_file()
        assert oct(final.stat().st_mode)[-3:] == "600"
    finally:
        app.state.engine.dispose()


def test_hive_identity_stable_after_restart(client, settings):
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    first = client.get("/api/v1/hive/identity").json()["instance_id"]

    app2 = __import__("mediatracker.api.app", fromlist=["create_app"]).create_app(settings)
    try:
        with TestClient(app2) as tc:
            login(tc)
            second = tc.get("/api/v1/hive/identity").json()["instance_id"]
            assert first == second
    finally:
        app2.state.engine.dispose()


def test_private_key_not_in_response_or_logs(caplog, client, settings):
    caplog.set_level("INFO")
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={
                "values": {
                    "hive_enabled": True,
                    "hive_activation_name": "ab",
                    "hive_public_address": "",
                }
            },
            headers=csrf(client),
        ).status_code
        == 200
    )
    secret_pem = hive_private_key_path(settings).read_bytes().decode("utf-8")
    assert "BEGIN PRIVATE KEY" in secret_pem
    identity = client.get("/api/v1/hive/identity")
    assert identity.status_code == 200
    body = identity.json()
    pub = body["public_key_pem"]
    assert pub
    assert "PRIVATE KEY" not in pub
    assert "BEGIN" in pub
    joined = " ".join(rec.getMessage() for rec in caplog.records)
    assert secret_pem not in joined


def test_name_required_then_no_modify(client):
    login(client)
    r = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True}},
        headers=csrf(client),
    )
    assert r.status_code == 422
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    denied = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_instance_name": "cd"}},
        headers=csrf(client),
    )
    assert denied.status_code == 422


def test_reset_clears_membership_and_new_identity(client):
    from mediatracker.db.models import HiveMember, HiveRuche, utcnow

    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    old_id = client.get("/api/v1/hive/identity").json()["instance_id"]

    db = client.app.state.session_factory()
    try:
        ruche_id = b"\x01" * 16
        db.add(HiveRuche(ruche_id=ruche_id, creator_instance_id=None, created_at=utcnow()))
        db.add(HiveMember(ruche_id=ruche_id, member_instance_id=old_id, joined_at=utcnow()))
        db.commit()
    finally:
        db.close()

    assert (
        client.post(
            "/api/v1/hive/identity/reset",
            json={"name": "nw"},
            headers=csrf(client),
        ).status_code
        == 204
    )
    payload = client.get("/api/v1/hive/identity").json()
    assert payload["instance_id"] != old_id
    assert payload["name"] == "nw"
    db = client.app.state.session_factory()
    try:
        assert db.query(HiveMember).count() == 0
    finally:
        db.close()


def test_hive_identity_lost_key_missing(client):
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    hive_private_key_path(client.app.state.settings).unlink()
    r = client.get("/api/v1/hive/identity")
    assert r.status_code == 200
    body = r.json()
    assert body["identity_lost"] is True
    assert body["identity_lost_message"]
    assert body["instance_id"] is None

    denied = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_public_address": "https://mtr.exemple.fr"}},
        headers=csrf(client),
    )
    assert denied.status_code == 422


def test_hive_cycle_off_on_does_not_regenerate_without_key(client):
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    hive_private_key_path(client.app.state.settings).unlink()
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": False}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    blocked = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True}},
        headers=csrf(client),
    )
    assert blocked.status_code == 422
    assert hive_private_key_path(client.app.state.settings).is_file() is False


def test_hive_reset_after_lost_restores_ok(client):
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    hive_private_key_path(client.app.state.settings).unlink()
    assert client.get("/api/v1/hive/identity").json()["identity_lost"] is True

    assert (
        client.post(
            "/api/v1/hive/identity/reset",
            json={"name": "nk"},
            headers=csrf(client),
        ).status_code
        == 204
    )
    ok = client.get("/api/v1/hive/identity").json()
    assert ok["identity_lost"] is False
    assert ok["instance_id"]
    assert ok["name"] == "nk"
    assert hive_private_key_path(client.app.state.settings).is_file()


def test_hive_impasse_lost_off_reset_then_on(client):
    """Identité perdue puis Hive éteint : reset autorisé, puis rallumage."""
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    hive_private_key_path(client.app.state.settings).unlink()
    assert client.get("/api/v1/hive/identity").json()["identity_lost"] is True
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": False}},
            headers=csrf(client),
        ).status_code
        == 200
    )

    assert client.post("/api/v1/hive/identity/reset", json={"name": "rx"}, headers=csrf(client)).status_code == 204
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    body = client.get("/api/v1/hive/identity").json()
    assert body["identity_lost"] is False
    assert body["name"] == "rx"
    assert body["instance_id"]


def test_hive_path_still_404_when_off_other_route(client):
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": False}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    assert client.get("/hive/other").status_code == 404


def test_hive_identity_lost_unreadable_key(client, monkeypatch):
    login(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": "ab"}},
            headers=csrf(client),
        ).status_code
        == 200
    )
    key_path = hive_private_key_path(client.app.state.settings)
    real_read = Path.read_bytes

    def _deny(self):
        if self.resolve() == key_path.resolve():
            raise PermissionError("denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", _deny)
    body = client.get("/api/v1/hive/identity").json()
    assert body["identity_lost"] is True
    assert body["instance_id"] is None


def test_hive_write_private_key_cleans_tmp_on_replace_fail(settings, monkeypatch):
    pk, _ = generate_keypair()

    def boom(_src, _dst, *_args, **_kwargs):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)

    tmp_dir = hive_data_dir(settings)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(OSError, match="replace failed"):
        write_private_key(settings, pk)
    assert not list(tmp_dir.glob(".identity.key.*.tmp"))

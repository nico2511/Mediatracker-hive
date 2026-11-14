"""S3g — invalidation des sessions navigateur."""

from fastapi.testclient import TestClient
from tests.conftest import csrf, login


def _other_client(app) -> TestClient:
    return TestClient(app)


def test_password_change_invalidates_other_sessions(client, app):
    login(client)
    other = _other_client(app)
    login(other)
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 200

    changed = client.post(
        "/api/v1/auth/password",
        json={"current_password": "test-admin-password", "new_password": "nouveau-mot-de-passe"},
        headers=csrf(client),
    )
    assert changed.status_code == 204

    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401

    login(client, password="nouveau-mot-de-passe")
    other.close()


def test_revoke_others_keeps_current_session(client, app):
    login(client)
    other = _other_client(app)
    login(other)
    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 200

    revoked = client.post("/api/v1/auth/sessions/revoke-others", headers=csrf(client))
    assert revoked.status_code == 204

    assert client.get("/api/v1/auth/me").status_code == 200
    assert other.get("/api/v1/auth/me").status_code == 401
    other.close()


def test_revoke_others_requires_authentication(client):
    assert client.post("/api/v1/auth/sessions/revoke-others").status_code == 401

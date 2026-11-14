"""Docker hits /healthz without a session. /api/v1/health needs read scope."""

from tests.conftest import csrf, login


def test_health_requires_auth_and_healthz_is_public(client):
    assert client.get("/api/v1/health").status_code == 401
    hidden = client.get("/api/v1/health/components")
    assert hidden.status_code == 401

    healthz = client.get("/healthz")
    assert healthz.status_code == 200
    body = healthz.json()
    assert body == {"status": "ok"}
    assert "version" not in body
    assert "sha" not in body

    login(client)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    body = health.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["version"] == "2.0.0"
    assert body["sha"] is None
    assert body["build_date"] is None

    detail = client.get("/api/v1/health/components")
    assert detail.status_code == 200, detail.text
    names = {row["name"] for row in detail.json()["components"]}
    assert names == {"aria2", "alldebrid", "tmdb", "jellyfin", "wawacity", "notifier", "disk", "library", "volumes"}
    blob = detail.text.lower()
    assert "test-secret-key" not in blob
    assert "bearer " not in blob


def test_health_and_summary_report_the_image_stamp(client, monkeypatch):
    monkeypatch.setenv("MEDIATRACKER_VERSION", "2.4.1")
    monkeypatch.setenv("MEDIATRACKER_GIT_SHA", "AbC1234fffdeadbeef")
    monkeypatch.setenv("MEDIATRACKER_BUILD_DATE", "2026-10-13T05:00:00Z")
    assert client.get("/api/v1/health").status_code == 401
    login(client)
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["version"] == "2.4.1"
    assert health.json()["sha"] == "abc1234"
    assert health.json()["build_date"] == "2026-10-13T05:00:00Z"

    token = client.post(
        "/api/v1/tokens",
        json={"name": "ha-version", "scopes": ["read"]},
        headers=csrf(client),
    )
    assert token.status_code == 201, token.text
    summary = client.get(
        "/api/v1/ha/summary",
        headers={"Authorization": f"Bearer {token.json()['token']}"},
    )
    assert summary.status_code == 200, summary.text
    assert summary.json()["build"] == {
        "version": "2.4.1",
        "sha": "abc1234",
        "build_date": "2026-10-13T05:00:00Z",
    }


def test_health_accepts_read_bearer_without_session(client):
    login(client)
    token = client.post(
        "/api/v1/tokens",
        json={"name": "probe-read", "scopes": ["read"]},
        headers=csrf(client),
    )
    assert token.status_code == 201, token.text
    client.cookies.clear()
    health = client.get(
        "/api/v1/health",
        headers={"Authorization": f"Bearer {token.json()['token']}"},
    )
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

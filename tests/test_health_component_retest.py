"""POST /api/v1/health/components/{name}/retest"""

from tests.conftest import csrf, login


def test_component_retest_requires_auth(client):
    assert client.post("/api/v1/health/components/library/retest").status_code == 401


def test_component_retest_unknown_name(client):
    login(client)
    response = client.post(
        "/api/v1/health/components/unknown-service/retest",
        headers=csrf(client),
    )
    assert response.status_code == 404, response.text
    assert "inconnu" in response.json()["detail"].lower()


def test_component_retest_runs_single_probe(client):
    login(client)
    response = client.post(
        "/api/v1/health/components/library/retest",
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["component"]["name"] == "library"
    assert body["component"]["status"] in {"ok", "degraded", "down", "skipped"}
    assert isinstance(body["component"]["message"], str)

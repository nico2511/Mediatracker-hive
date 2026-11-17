"""GET /settings exposes numeric bounds and non-secret defaults."""

from tests.conftest import login


def test_settings_list_includes_bounds_and_defaults_not_secrets(client):
    login(client)
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    by_key = {item["key"]: item for item in response.json()["settings"]}

    backoff = by_key["source_error_backoff_min"]
    assert backoff["min"] == 30
    assert backoff["max"] == 1440
    assert backoff["default"] == client.app.state.settings.source_error_backoff_min

    scan = by_key["scan_interval_min"]
    assert scan["min"] == 60
    assert scan["max"] == 10080
    assert scan["default"] == client.app.state.settings.scan_interval_min

    secret = by_key["aria2_rpc_secret"]
    assert secret["secret"] is True
    assert "default" not in secret
    assert "min" not in secret
from tests.conftest import csrf, login


def test_token_is_shown_once_and_scopes_apply(client):
    login(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": "script", "scopes": ["read"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert token.startswith("mt_")
    listed = client.get("/api/v1/tokens")
    assert listed.status_code == 200
    assert "token" not in listed.json()["items"][0]
    assert listed.json()["items"][0]["prefix"] == created.json()["prefix"]

    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/settings", headers=headers).status_code == 200
    denied = client.patch("/api/v1/settings", json={"values": {"scan_interval_min": 720}}, headers=headers)
    assert denied.status_code == 403
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    login(client)
    token_id = created.json()["id"]
    assert client.delete(f"/api/v1/tokens/{token_id}", headers=csrf(client)).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_settings_mask_secrets_and_scan_defaults(client):
    login(client)
    bare = client.patch("/api/v1/settings", json={"values": {"default_quality": "1080p"}})
    assert bare.status_code == 403
    response = client.get("/api/v1/settings")
    values = {item["key"]: item for item in response.json()["settings"]}
    assert values["scan_interval_min"]["value"] == 720
    assert values["source_request_delay_seconds"]["value"] == 5
    assert values["scan_air_window_days"]["value"] == 3
    assert values["scan_ongoing_only"]["value"] is True
    assert values["aria2_rpc_secret"]["value"] is None
    assert values["aria2_rpc_secret"]["set"] is True
    assert values["tmdb_read_token"]["secret"] is True
    assert values["tmdb_read_token"]["set"] is False

    updated = client.patch(
        "/api/v1/settings",
        json={"values": {"scan_interval_min": 15}},
        headers=csrf(client),
    )
    assert updated.status_code == 422
    ok = client.patch(
        "/api/v1/settings",
        json={"values": {"tmdb_read_token": "tmdb-test-token", "scan_ongoing_only": False}},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text
    again = {item["key"]: item for item in ok.json()["settings"]}
    assert again["tmdb_read_token"]["value"] is None
    assert again["tmdb_read_token"]["set"] is True
    assert again["scan_ongoing_only"]["value"] is False

    from sqlalchemy import select

    from mediatracker.db.models import Setting

    db = client.app.state.session_factory()
    try:
        row = db.scalar(select(Setting).where(Setting.key == "tmdb_read_token"))
        assert row.value.startswith("enc:v1:")
        assert "tmdb-test-token" not in row.value
    finally:
        db.close()

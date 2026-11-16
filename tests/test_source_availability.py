"""Garde AllDebrid / Hive : sources utilisables, scan, recherche, téléchargement."""

from __future__ import annotations

from tests.conftest import FICTIVE_ALLDEBRID_API_KEY, csrf, login

from mediatracker.config import get_settings
from mediatracker.core.source_availability import (
    DOWNLOAD_BLOCKED_CODE,
    DOWNLOAD_BLOCKED_MESSAGE,
    any_download_source_available,
    download_source_enabled_in_settings,
    filter_usable_source_order,
    source_usable,
)
from mediatracker.integrations.alldebrid_account import is_connected
from mediatracker.providers.sources.loading import remote_links_source, sources_for_catalog


def test_without_alldebrid_non_hive_sources_blocked(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        assert not is_connected(app.state.settings, db)
        assert not any_download_source_available(db, app.state.settings)
        for name in ("wawacity", "free_telecharger", "comet", "torrentio"):
            assert not source_usable(db, app.state.settings, name)
        assert remote_links_source(db, app.state.settings, "wawacity") is None
        assert sources_for_catalog(db, app.state.settings) == []
        filtered = filter_usable_source_order(db, app.state.settings, ["wawacity", "comet", "hive"])
        assert filtered == []
    finally:
        db.close()


def test_download_ready_requires_enabled_and_usable_source(client, app, monkeypatch):
    monkeypatch.setenv("ALLDEBRID_API_KEY", FICTIVE_ALLDEBRID_API_KEY)
    from mediatracker.config import get_settings

    app.state.settings = get_settings()
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "hive"}},
        headers=csrf(client),
    )
    body = client.get("/api/v1/settings/sources/status", headers=csrf(client)).json()
    assert body["download_ready"] is False


def test_episode_source_blocked_without_usable_gate(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        from mediatracker.providers.sources.loading import episode_source

        assert episode_source("comet", app.state.settings, db=db, base_url="https://exemple.invalid") is None
    finally:
        db.close()


def test_status_endpoint_marks_sources_unavailable(client):
    login(client)
    body = client.get("/api/v1/settings/sources/status", headers=csrf(client)).json()
    assert body["download_ready"] is False
    assert body["blocked_message"] == DOWNLOAD_BLOCKED_MESSAGE
    assert body["available_sources"] == []
    wawa = next(row for row in body["sources"] if row["source"] == "wawacity")
    assert wawa["available"] is False
    assert wawa["enabled"] is True


def test_check_media_refused_without_usable_source(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film-Démo", "year": 2099, "followed": True},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    mid = created.json()["id"]
    response = client.post(f"/api/v1/media/{mid}/check", headers=csrf(client))
    assert response.status_code == 422, response.text
    payload = response.json()
    assert payload["detail"] == DOWNLOAD_BLOCKED_MESSAGE
    assert payload.get("code") == DOWNLOAD_BLOCKED_CODE


def test_force_download_refused_without_usable_source(client):
    login(client)
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Film-Démo", "year": 2099, "followed": True},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    mid = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{mid}/items",
        json={"title": "Version principale"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    item_id = item.json()["id"]
    response = client.post(
        f"/api/v1/media/{mid}/force-download",
        json={"item_id": item_id},
        headers=csrf(client),
    )
    assert response.status_code == 422, response.text
    assert response.json().get("code") == DOWNLOAD_BLOCKED_CODE


def test_env_alldebrid_key_unlocks_gated_sources(client, app, monkeypatch):
    monkeypatch.setenv("ALLDEBRID_API_KEY", FICTIVE_ALLDEBRID_API_KEY)
    app.state.settings = get_settings()
    login(client)
    db = app.state.session_factory()
    try:
        assert is_connected(app.state.settings, db)
        assert source_usable(db, app.state.settings, "wawacity")
        assert any_download_source_available(db, app.state.settings)
    finally:
        db.close()
    body = client.get("/api/v1/settings/sources/status", headers=csrf(client)).json()
    assert "wawacity" in body["available_sources"]


def test_globally_disabled_source_not_enabled_in_settings(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity,comet"}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    try:
        assert download_source_enabled_in_settings(db, app.state.settings, "free_telecharger") is False
        assert download_source_enabled_in_settings(db, app.state.settings, "wawacity") is True
    finally:
        db.close()


def test_hive_usable_only_when_enabled(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        assert not source_usable(db, app.state.settings, "hive")
    finally:
        db.close()
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_enabled": True, "hive_activation_name": "ab", "hive_public_address": "https://mtr.exemple.fr"}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    try:
        assert source_usable(db, app.state.settings, "hive")
        assert any_download_source_available(db, app.state.settings)
    finally:
        db.close()

"""Garde AllDebrid / Hive : sources utilisables, scan, recherche, téléchargement."""

from __future__ import annotations

from tests.conftest import FICTIVE_ALLDEBRID_API_KEY, connect_alldebrid_for_tests, csrf, login

from mediatracker.config import get_settings
from mediatracker.core.source_availability import (
    DOWNLOAD_BLOCKED_CODE,
    DOWNLOAD_BLOCKED_MESSAGE,
    any_download_source_available,
    download_source_enabled_in_settings,
    filter_usable_source_order,
    global_enabled_download_sources,
    source_unusable_reason,
    source_usable,
)
from mediatracker.core.source_poll import close_source
from mediatracker.integrations.alldebrid_account import is_connected
from mediatracker.providers.sources.loading import enabled_remote_sources, remote_links_source, sources_for_catalog


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


def test_global_enabled_download_sources_fail_closed_on_unknown_tokens(client, app, caplog):
    import logging
    from datetime import datetime, timezone

    from mediatracker.db.models import Setting

    db = app.state.session_factory()
    try:
        row = db.get(Setting, "download_source_order")
        if row is None:
            row = Setting(key="download_source_order", value="")
            db.add(row)
        row.value = "wawacity,source-demo-inconnue,comet"
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        with caplog.at_level(logging.WARNING):
            enabled = global_enabled_download_sources(db, app.state.settings)
        assert enabled == ["wawacity", "comet"]
        assert any("source-demo-inconnue" in rec.message for rec in caplog.records)
    finally:
        db.close()


def test_global_enabled_download_sources_all_unknown_tokens_empty(client, app):
    from datetime import datetime, timezone

    from mediatracker.db.models import Setting

    db = app.state.session_factory()
    try:
        row = db.get(Setting, "download_source_order")
        if row is None:
            row = Setting(key="download_source_order", value="")
            db.add(row)
        row.value = "source-demo-a,source-demo-b"
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        assert global_enabled_download_sources(db, app.state.settings) == []
    finally:
        db.close()


def test_enabled_remote_sources_excludes_globally_disabled(client, app):
    from tests.conftest import connect_alldebrid_for_tests

    login(client)
    connect_alldebrid_for_tests(app)
    client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity,comet"}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    opened: list[tuple[int, object]] = []
    try:
        opened = enabled_remote_sources(db, app.state.settings)
        names = {getattr(src, "type", None) for _, src in opened}
        assert "free_telecharger" not in names
    finally:
        for _, src in opened:
            close_source(src)
        db.close()


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


def _enable_hive(client):
    client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "hive_activation_name": "ab",
                "hive_public_address": "https://mtr.exemple.fr",
            }
        },
        headers=csrf(client),
    )


def test_hive_only_blocks_remote_when_alldebrid_connected(client, app):
    login(client)
    connect_alldebrid_for_tests(app)
    _enable_hive(client)
    client.patch("/api/v1/settings", json={"values": {"hive_only": True}}, headers=csrf(client))
    db = app.state.session_factory()
    try:
        assert is_connected(app.state.settings, db)
        assert not source_usable(db, app.state.settings, "wawacity")
        assert not source_usable(db, app.state.settings, "comet")
        assert source_usable(db, app.state.settings, "hive")
        assert remote_links_source(db, app.state.settings, "wawacity") is None
        assert sources_for_catalog(db, app.state.settings) == []
        filtered = filter_usable_source_order(db, app.state.settings, ["wawacity", "hive", "comet"])
        assert filtered == ["hive"]
    finally:
        db.close()


def test_hive_only_false_keeps_alldebrid_unlock(client, app):
    login(client)
    connect_alldebrid_for_tests(app)
    db = app.state.session_factory()
    try:
        assert source_usable(db, app.state.settings, "wawacity")
    finally:
        db.close()


def test_hive_only_persists_and_rejects_without_hive(client, app):
    login(client)
    denied = client.patch("/api/v1/settings", json={"values": {"hive_only": True}}, headers=csrf(client))
    assert denied.status_code == 422, denied.text
    _enable_hive(client)
    ok = client.patch("/api/v1/settings", json={"values": {"hive_only": True}}, headers=csrf(client))
    assert ok.status_code == 200, ok.text
    hive_only = next(row for row in ok.json()["settings"] if row["key"] == "hive_only")
    assert hive_only["value"] is True


def test_manual_probe_ignores_hive_only_gate(client, app):
    login(client)
    connect_alldebrid_for_tests(app)
    _enable_hive(client)
    client.patch("/api/v1/settings", json={"values": {"hive_only": True}}, headers=csrf(client))
    db = app.state.session_factory()
    try:
        assert not source_usable(db, app.state.settings, "wawacity")
        assert "Ruche seule" in (source_unusable_reason(db, app.state.settings, "wawacity") or "")
        assert source_usable(db, app.state.settings, "wawacity", for_manual_probe=True)
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

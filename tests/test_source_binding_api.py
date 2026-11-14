"""W4: HTTP API for Wawacity source bindings."""

from datetime import datetime, timezone

import httpx
from tests.conftest import csrf, login
from tests.test_wawacity import BASE, NOW, _text

from mediatracker.db.models import Provider, SourceBinding

WRONG_BASE = "https://autre-domaine.test"
PAGE_PATH = "/film/fictif-2024-hd-vff"


def _seed_film(client) -> int:
    created = client.post(
        "/api/v1/media",
        json={
            "type": "film",
            "title": "Fictif",
            "year": 2024,
            "new_episode_policy": "auto",
            "next_air_date": "2026-10-10",
            "pref_quality": "1080p",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _seed_series(client) -> int:
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Série Fictive",
            "year": 2024,
            "new_episode_policy": "auto",
            "next_air_date": "2026-10-10",
            "pref_quality": "1080p",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _binding(db, media_id: int, provider_id: int, *, path: str, season: int | None = None) -> None:
    row = SourceBinding(
        media_id=media_id,
        provider_id=provider_id,
        external_ref=path[:300],
        url=f"{BASE}{path}",
        path=path,
        quality="1080p",
        language="VFF",
        season=season,
        active=True,
        last_scanned_at=NOW,
        last_error="ancienne erreur",
        last_error_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    db.add(row)
    db.commit()


def test_get_film_binding_recomposes_url(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    media_id = _seed_film(client)
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(db, media_id, provider.id, path=PAGE_PATH)
    finally:
        db.close()

    response = client.get(f"/api/v1/media/{media_id}/source-binding")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == PAGE_PATH
    assert body["url"] == f"{BASE}{PAGE_PATH}"
    assert body["state"] == "error"
    assert body["last_error"] == "ancienne erreur"


def test_put_binding_path_clears_error_and_marks_manual(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE}},
        headers=csrf(client),
    )
    media_id = _seed_film(client)

    response = client.put(
        f"/api/v1/media/{media_id}/source-binding",
        json={"path": PAGE_PATH},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["path"] == PAGE_PATH
    assert body["url"] == f"{BASE}{PAGE_PATH}"
    assert body["state"] == "manual"
    assert body["manual"] is True
    assert body["last_error"] is None

    db = app.state.session_factory()
    try:
        row = db.query(SourceBinding).filter(SourceBinding.media_id == media_id).one()
        assert row.path == PAGE_PATH
        assert row.path_manual is True
        assert row.last_error is None
    finally:
        db.close()


def test_put_rejects_foreign_domain(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE}},
        headers=csrf(client),
    )
    media_id = _seed_film(client)
    response = client.put(
        f"/api/v1/media/{media_id}/source-binding",
        json={"url": f"{WRONG_BASE}{PAGE_PATH}"},
        headers=csrf(client),
    )
    assert response.status_code == 422, response.text
    assert "domaine Wawacity" in response.json()["detail"]


def test_probe_single_get_and_allowed_hosters_only(client, app, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    media_id = _seed_film(client)
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(db, media_id, provider.id, path="/film/archipel-pilote-2021-hd-vff")
    finally:
        db.close()

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=_text("film_hd_vff.html"))

    transport = httpx.MockTransport(handler)
    app.state.wawacity_test_transport = transport

    response = client.post(
        f"/api/v1/media/{media_id}/source-binding/test",
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    body = response.json()
    assert body["accessible"] is True
    assert body["title"]
    assert "1fichier" in body["links_by_hoster"]
    assert "uptobox" not in body["links_by_hoster"]
    assert body["links_by_hoster"].get("rapidgator", 0) >= 1


def test_season_binding_read_and_update(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE}},
        headers=csrf(client),
    )
    media_id = _seed_series(client)
    season_path = "/serie/fictive-saison-1-hd-vff"
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        _binding(db, media_id, provider.id, path=season_path, season=1)
    finally:
        db.close()

    read = client.get(f"/api/v1/media/{media_id}/seasons/1/source-binding")
    assert read.status_code == 200
    assert read.json()["path"] == season_path

    updated = client.patch(
        f"/api/v1/media/{media_id}/seasons/1/source-binding",
        json={"url": f"{BASE}/serie/fictive-saison-1-vff"},
        headers=csrf(client),
    )
    assert updated.status_code == 200
    assert updated.json()["path"] == "/serie/fictive-saison-1-vff"
    assert updated.json()["manual"] is True

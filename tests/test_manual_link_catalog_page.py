"""Lien manuel : une URL de page catalogue devient une liaison source, pas un lien direct."""

from pathlib import Path
from unittest.mock import MagicMock

import httpx
from tests.conftest import csrf, login
from tests.test_wawacity import BASE, _poll, _text

from mediatracker.core.source_binding import match_catalog_page_url
from mediatracker.db.models import Link, Provider, SourceBinding

FILM_PAGE = "/film/fictif-demo-2024-hd-vff"
SERIES_PAGE = "/serie/titre-numerique-saison-1-hd-vff"
FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"


def test_manual_link_film_page_becomes_binding(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Fictif Démo", "year": 2024, "followed": True},
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    rsp = client.post(
        "/api/v1/links",
        json={"media_id": media_id, "url": f"{BASE}{FILM_PAGE}", "start": True},
        headers=csrf(client),
    )
    assert rsp.status_code == 201, rsp.text
    body = rsp.json()
    assert body["source_binding"] is True
    assert body["kind"] == "source_page"
    db = app.state.session_factory()
    try:
        assert db.query(Link).count() == 0
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        row = (
            db.query(SourceBinding)
            .filter(SourceBinding.media_id == media_id, SourceBinding.provider_id == provider.id)
            .one()
        )
        assert row.path == FILM_PAGE
        assert row.path_manual is True
    finally:
        db.close()


def test_manual_link_series_season_page_binding_and_poll(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "AgentFictif/1.0"}},
        headers=csrf(client),
    )
    created = client.post(
        "/api/v1/media",
        json={
            "type": "series",
            "title": "Démo Série",
            "year": 2024,
            "followed": True,
            "pref_quality": "HD",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    media_id = created.json()["id"]
    client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "air_date": "2026-10-11"},
        headers=csrf(client),
    )
    linked = client.post(
        "/api/v1/links",
        json={"media_id": media_id, "url": f"{BASE}{SERIES_PAGE}", "start": True},
        headers=csrf(client),
    )
    assert linked.status_code == 201
    assert linked.json()["source_binding"] is True
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        url = str(request.url)
        if "search=" in url:
            return httpx.Response(200, text=_text("search_numeric_decoys.html"))
        if SERIES_PAGE in url:
            return httpx.Response(200, text=(FIXTURES / "series_numeric_s1.html").read_text(encoding="utf-8"))
        return httpx.Response(404, text="absent")

    report = _poll(app, media_id, handler)
    assert report.result == "found"
    assert sum(1 for url in calls if "search=" in url) == 0
    assert any(SERIES_PAGE in url for url in calls)


def test_manual_hoster_link_stays_ddl(client, app):
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Hoster Seul", "year": 2026},
        headers=csrf(client),
    )
    media_id = media.json()["id"]
    rsp = client.post(
        "/api/v1/links",
        json={"media_id": media_id, "url": "https://1fichier.com/file/fictif", "start": False},
        headers=csrf(client),
    )
    assert rsp.status_code == 201
    assert rsp.json()["source_binding"] is False
    assert rsp.json()["kind"] == "ddl_hoster"
    db = app.state.session_factory()
    try:
        assert db.query(Link).count() == 1
        assert db.query(SourceBinding).count() == 0
    finally:
        db.close()


def test_manual_page_binding_wakes_worker(client, app, monkeypatch):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE}},
        headers=csrf(client),
    )
    woke = MagicMock()
    monkeypatch.setattr("mediatracker.api.v1.links.wake_worker", woke)
    media = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Réveil Page", "year": 2026, "followed": True},
        headers=csrf(client),
    )
    media_id = media.json()["id"]
    rsp = client.post(
        "/api/v1/links",
        json={"media_id": media_id, "url": f"{BASE}{FILM_PAGE}", "start": True},
        headers=csrf(client),
    )
    assert rsp.status_code == 201
    assert rsp.json()["source_binding"] is True
    assert woke.call_count >= 1


def test_match_catalog_page_url_with_app_db(client, app):
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE}},
        headers=csrf(client),
    )
    db = app.state.session_factory()
    try:
        hit = match_catalog_page_url(db, app.state.settings, f"{BASE}{FILM_PAGE}")
        assert hit is not None
        assert hit.source_type == "wawacity"
        assert hit.stored_path == FILM_PAGE
        assert hit.media_kind == "film"
    finally:
        db.close()

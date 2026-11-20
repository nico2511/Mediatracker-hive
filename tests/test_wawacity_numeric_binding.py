"""Série au titre numérique et liaison manuelle Wawacity (pas de recherche par titre)."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
from tests.conftest import csrf, login
from tests.test_wawacity import BASE, NOW, _poll, _text

from mediatracker.core.source_binding import pick_bound_pages
from mediatracker.core.source_select import identity_match, titles_match
from mediatracker.db.models import Download, Media, MediaItem, Provider, SourceBinding

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
NUMERIC_PAGE = "/serie/titre-numerique-saison-1-hd-vff"


def _is_search(url: str) -> bool:
    return "search=" in url


def _handler(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        url = str(request.url)
        if "search=" in url:
            return httpx.Response(200, text=_text("search_numeric_decoys.html"))
        if NUMERIC_PAGE in url:
            return httpx.Response(200, text=(FIXTURES / "series_numeric_s1.html").read_text(encoding="utf-8"))
        return httpx.Response(404, text="absent")

    return handler


def test_manual_binding_used_without_revalidation_scan(client, app):
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
            "title": "8 8 8",
            "year": 2024,
            "tmdb_id": 900777,
            "followed": True,
            "series_status": "returning",
            "new_episode_policy": "propose",
            "pref_quality": "HD",
            "pref_language": "VFF",
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    media_id = created.json()["id"]
    item = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 1, "air_date": "2026-10-11"},
        headers=csrf(client),
    )
    assert item.status_code == 201, item.text
    bound = client.patch(
        f"/api/v1/media/{media_id}/seasons/1/source-binding",
        json={"path": f"{NUMERIC_PAGE}"},
        headers=csrf(client),
    )
    assert bound.status_code == 200, bound.text
    assert bound.json()["manual"] is True

    calls: list[str] = []
    report = _poll(app, media_id, _handler(calls))
    assert report.result == "found"
    assert sum(1 for url in calls if _is_search(url)) == 0
    assert any(NUMERIC_PAGE in url for url in calls)


def test_pick_bound_pages_accepts_manual_without_last_scan(client, app):
    del client
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        media = Media(type="series", title="9 9 9", year=2024, followed=True)
        db.add(media)
        db.flush()
        db.add(
            SourceBinding(
                media_id=media.id,
                provider_id=provider.id,
                external_ref=NUMERIC_PAGE,
                url=f"{BASE}{NUMERIC_PAGE}",
                path=NUMERIC_PAGE,
                path_manual=True,
                active=True,
                season=1,
            )
        )
        db.commit()
        pages = pick_bound_pages(
            db,
            app.state.settings,
            media,
            provider.id,
            {1},
            now=NOW,
            force_search=False,
        )
        assert pages is not None
        assert len(pages) == 1
        assert pages[0].url == f"{BASE}{NUMERIC_PAGE}"
    finally:
        db.close()


def test_identity_match_numeric_title_strict():
    assert titles_match("8 8 8", "8 8 8")
    assert not titles_match("8 8 8", "8")
    assert not titles_match("8 8 8", "Long Titre 8")
    assert identity_match(
        "8 8 8",
        2024,
        title="8 8 8",
        original_title=None,
        year=2024,
        media_type="series",
    )
    assert not identity_match(
        "8",
        2024,
        title="8 8 8",
        original_title=None,
        year=2024,
        media_type="series",
    )
    assert not identity_match(
        "8 8 8",
        2011,
        title="8 8 8",
        original_title=None,
        year=2024,
        media_type="series",
    )
    assert identity_match(
        "8 8 8",
        2024,
        title="8 8 8",
        original_title=None,
        year=2024,
        media_type="series",
        tmdb_id=900777,
        candidate_ref="/serie/titre-900777-saison-1",
    )


def test_manual_link_without_slot_not_marked_available(client, app):
    login(client)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Sans Fiche", "year": 2026},
        headers=csrf(client),
    )
    assert media.status_code == 201
    media_id = media.json()["id"]
    linked = client.post(
        "/api/v1/links",
        json={
            "media_id": media_id,
            "url": "https://1fichier.com/file/phantom",
            "start": True,
        },
        headers=csrf(client),
    )
    assert linked.status_code == 201
    assert linked.json()["needs_slot"] is True
    assert linked.json()["download_id"] is None
    db = app.state.session_factory()
    try:
        item = db.get(MediaItem, linked.json()["media_item_id"])
        assert item is not None
        assert item.state == "ambiguous"
        assert item.source_available is not True
        assert db.query(Download).count() == 0
    finally:
        db.close()


def test_manual_link_attached_to_active_download_wakes_worker(client, app, monkeypatch):
    login(client)
    woke = MagicMock()
    monkeypatch.setattr("mediatracker.api.v1.links.wake_worker", woke)
    media = client.post(
        "/api/v1/media",
        json={"type": "series", "title": "Rattache Démo", "year": 2026},
        headers=csrf(client),
    )
    media_id = media.json()["id"]
    item_rsp = client.post(
        f"/api/v1/media/{media_id}/items",
        json={"season": 1, "episode": 2},
        headers=csrf(client),
    )
    item_id = item_rsp.json()["id"]
    db = app.state.session_factory()
    try:
        download = Download(
            media_item_id=item_id,
            state="downloading",
            created_at=datetime.now(timezone.utc),
        )
        db.add(download)
        db.commit()
        download_id = download.id
    finally:
        db.close()

    linked = client.post(
        "/api/v1/links",
        json={
            "media_id": media_id,
            "url": "https://1fichier.com/file/rattach",
            "season": 1,
            "episode": 2,
            "start": True,
        },
        headers=csrf(client),
    )
    assert linked.status_code == 201
    assert linked.json()["download_id"] == download_id
    db = app.state.session_factory()
    try:
        row = db.get(Download, download_id)
        assert row is not None
        assert row.link_id == linked.json()["id"]
    finally:
        db.close()
    assert woke.call_count >= 1
"""Catalogue : résolution d'affiches TMDB, cache, repli Wawacity et vignette titre."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from tests.conftest import connect_alldebrid_for_tests, csrf, login

from mediatracker.core.catalog import refresh_catalog
from mediatracker.core.catalog_poster import (
    resolve_catalog_posters,
    tmdb_cache_key,
)
from mediatracker.db.models import CatalogEntry, CatalogTmdbCache
from mediatracker.integrations.tmdb import TmdbHit
from mediatracker.providers.base import ReleaseListing
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.providers.sources.wawacity_html import parse_listing

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
BASE = "https://wawa.test"
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _catalog_sources_usable(app, client):
    del client
    connect_alldebrid_for_tests(app)


def _db(app):
    return app.state.session_factory()


def _listing(**kwargs) -> ReleaseListing:
    payload = {
        "title": "Exemple film",
        "year": 2024,
        "media_type": "film",
        "quality": "HD",
        "language": "VFF",
        "url": f"{BASE}/film/exemple-film",
        "ref": f"{BASE}/film/exemple-film",
    }
    payload.update(kwargs)
    return ReleaseListing(**payload)


class _TmdbStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search(self, title: str, media_type: str):
        self.calls.append((title, media_type))
        if title == "Exemple film":
            return [
                TmdbHit(
                    tmdb_id=9001,
                    media_type="film",
                    title="Exemple film",
                    year=2024,
                    overview=None,
                    poster_url="https://image.tmdb.org/t/p/w500/exemple.jpg",
                )
            ]
        return []

    def genre_names(self, media_type: str):
        return {}


def test_parse_listing_reads_block_cover():
    html = FIXTURES / "latest_films_with_covers.html"
    hits = parse_listing(html.read_text(encoding="utf-8"), BASE, "film")
    by_title = {hit.title: hit for hit in hits}
    assert by_title["Exemple film"].poster_url == "/covers/exemple-film.jpg"
    assert by_title["Archipel Pilote"].poster_url is None


def test_block_poster_rejects_non_http_url():
    html = """<div class="wa-sub-block">
      <div class="wa-sub-block-cover"><img src="javascript:alert(1)" /></div>
      <div class="wa-sub-block-title"><a href="/film/x">Films » Exemple film [HD] [VFF] (2024)</a></div>
    </div>"""
    hits = parse_listing(html, BASE, "film")
    assert hits[0].poster_url is None


def test_title_card_svg_valid_xml_without_attribute_injection(client, app):
    from xml.etree import ElementTree as ET

    db = _db(app)
    nasty = 'Exemple "guillemets" \'apostrophe\' & <balise>'
    row = CatalogEntry(
        dedupe_key="nasty|2024|film||",
        title=nasty,
        title_folded="nasty",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url=f"{BASE}/film/nasty",
        quality="HD",
        language="VFF",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    db.commit()
    login(client)
    response = client.get(f"/api/v1/images/title-card/{row.id}", headers=csrf(client))
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    root = ET.fromstring(response.content)
    assert root.tag.endswith("svg")
    assert root.get("aria-label") == nasty[:80]
    assert root.findtext(".//{http://www.w3.org/2000/svg}tspan") == nasty[:80]


def test_tmdb_cache_used_on_second_resolve(client, app):
    del client
    db = _db(app)
    row = CatalogEntry(
        dedupe_key="exemple|2024|film||",
        title="Exemple film",
        title_folded="exemple film",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url=f"{BASE}/film/exemple",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    db.commit()
    tmdb = _TmdbStub()
    settings = app.state.settings
    resolve_catalog_posters(db, settings, [row], now=NOW, tmdb=tmdb, limit=5)
    db.commit()
    assert row.poster_url is not None
    assert len(tmdb.calls) == 1
    tmdb.calls.clear()
    row.poster_url = None
    resolve_catalog_posters(db, settings, [row], now=NOW + timedelta(hours=1), tmdb=tmdb, limit=5)
    assert row.poster_url is not None
    assert tmdb.calls == []


def test_list_catalog_uses_wawacity_poster_without_extra_fetch(client, app, monkeypatch):
    db = _db(app)
    row = CatalogEntry(
        dedupe_key="exemple|2024|film||",
        title="Exemple film",
        title_folded="exemple film",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url=f"{BASE}/film/exemple",
        quality="HD",
        language="VFF",
        source_poster_url=f"{BASE}/covers/exemple-film.jpg",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    key = tmdb_cache_key(row)
    db.add(
        CatalogTmdbCache(
            cache_key=key,
            found=False,
            tmdb_id=None,
            poster_url=None,
            checked_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
    )
    db.commit()

    calls: list[str] = []

    def _forbidden(*_args, **_kwargs):
        calls.append("wawacity")
        raise AssertionError("pas de requête Wawacity supplémentaire")

    monkeypatch.setattr(WawacitySource, "_get", _forbidden)
    login(client)
    response = client.get("/api/v1/catalog?type=film", headers=csrf(client))
    assert response.status_code == 200
    item = next(row for row in response.json()["items"] if row["title"] == "Exemple film")
    assert item["poster_url"] == f"/api/v1/images/source/{row.id}"
    assert calls == []


def test_list_catalog_title_card_when_no_poster(client, app):
    db = _db(app)
    row = CatalogEntry(
        dedupe_key="autre|2024|film||",
        title="Autre exemple",
        title_folded="autre exemple",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url=f"{BASE}/film/autre",
        quality="HD",
        language="VFF",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
        tmdb_checked_at=NOW,
    )
    db.add(row)
    db.add(
        CatalogTmdbCache(
            cache_key=tmdb_cache_key(row),
            found=False,
            tmdb_id=None,
            poster_url=None,
            checked_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
    )
    db.commit()
    login(client)
    response = client.get("/api/v1/catalog?type=film", headers=csrf(client))
    item = next(row for row in response.json()["items"] if row["title"] == "Autre exemple")
    assert item["poster_url"] == f"/api/v1/images/title-card/{row.id}"


def test_refresh_stores_source_poster_from_listing(client, app):
    del client
    from sqlalchemy import select

    html = (FIXTURES / "latest_films_with_covers.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html, request=request)

    transport = httpx.MockTransport(handler)
    source = WawacitySource(WawacityConfig(base_url=BASE), transport=transport)
    feed = MagicMock()
    feed.capabilities = frozenset({"latest"})
    feed.latest_releases = source.latest_releases
    feed.close = lambda: None

    db = _db(app)
    refresh_catalog(db, app.state.settings, sources=[(1, "wawacity", feed)], tmdb=_TmdbStub(), force=True)
    row = db.scalar(select(CatalogEntry).where(CatalogEntry.title == "Exemple film"))
    assert row is not None
    assert row.source_poster_url == "/covers/exemple-film.jpg"


def test_stale_tmdb_miss_cache_ignored_after_key_version(client, app):
    del client
    db = _db(app)
    row = CatalogEntry(
        dedupe_key="fr|2025|film||",
        title="Exemple Titre - FRENCH",
        title_folded="exemple titre french",
        year=2025,
        media_type="film",
        source="wawacity",
        source_url=f"{BASE}/film/exemple-fr",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    db.commit()
    legacy_key = f"film|2025|{row.title_folded}"
    db.add(
        CatalogTmdbCache(
            cache_key=legacy_key,
            found=False,
            tmdb_id=None,
            poster_url=None,
            checked_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
    )
    db.commit()
    class _FrenchTmdb(_TmdbStub):
        def search(self, title: str, media_type: str):
            self.calls.append((title, media_type))
            if title == "Exemple Titre":
                return [
                    TmdbHit(
                        tmdb_id=9002,
                        media_type="film",
                        title="Exemple Titre",
                        year=2025,
                        overview=None,
                        poster_url="https://image.tmdb.org/t/p/w500/exemple-fr.jpg",
                    )
                ]
            return []

    french_tmdb = _FrenchTmdb()
    resolve_catalog_posters(db, app.state.settings, [row], now=NOW, tmdb=french_tmdb, limit=5)
    assert row.poster_url is not None
    assert french_tmdb.calls


def test_block_poster_prefers_data_lazy_src():
    html = """<div class="wa-sub-block">
      <div class="wa-sub-block-cover"><img src="/img/blank.gif" data-lazy-src="/covers/lazy2.jpg" /></div>
      <div class="wa-sub-block-title"><a href="/film/lazy2">Films » Exemple lazy deux [HD] (2024)</a></div>
    </div>"""
    hits = parse_listing(html, BASE, "film")
    assert hits[0].poster_url == "/covers/lazy2.jpg"


def test_tmdb_cache_migration_runs_once(client, app):
    del client
    from mediatracker.core.catalog_poster import _TMDB_CACHE_RESET_KEY, migrate_tmdb_poster_cache_once
    from mediatracker.db.models import Setting

    db = _db(app)
    prior = db.get(Setting, _TMDB_CACHE_RESET_KEY)
    if prior is not None:
        db.delete(prior)
        db.commit()
    row = CatalogEntry(
        dedupe_key="once|2024|film||",
        title="Exemple once",
        title_folded="exemple once",
        year=2024,
        media_type="film",
        source="wawacity",
        source_url=f"{BASE}/film/once",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    db.add(row)
    key_v1 = f"film|2024|{row.title_folded}"
    db.add(
        CatalogTmdbCache(
            cache_key=key_v1,
            found=False,
            tmdb_id=None,
            poster_url=None,
            checked_at=NOW,
            expires_at=NOW + timedelta(days=7),
        )
    )
    db.commit()
    removed = migrate_tmdb_poster_cache_once(db, now=NOW)
    db.commit()
    assert removed == 1
    assert db.get(CatalogTmdbCache, key_v1) is None
    assert migrate_tmdb_poster_cache_once(db, now=NOW) == 0


def test_block_poster_prefers_data_src_over_data_placeholder():
    placeholder = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    html = f"""<div class="wa-sub-block">
      <div class="wa-sub-block-cover"><img src="{placeholder}" data-src="/covers/lazy.jpg" /></div>
      <div class="wa-sub-block-title"><a href="/film/lazy">Films » Exemple lazy [HD] (2024)</a></div>
    </div>"""
    hits = parse_listing(html, BASE, "film")
    assert hits[0].poster_url == "/covers/lazy.jpg"

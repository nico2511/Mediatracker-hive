"""Catalogue : deux entrées de même clé de cache TMDB dans une même passe (pas d'IntegrityError)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from tests.conftest import connect_alldebrid_for_tests, csrf, login

from mediatracker.core.catalog import catalog_clock
from mediatracker.core.catalog_poster import _write_cache, resolve_catalog_posters, tmdb_cache_key
from mediatracker.db.models import CatalogEntry, CatalogTmdbCache, Setting
from mediatracker.integrations.tmdb import TmdbHit
from mediatracker.providers.base import ReleaseListing

BASE = "https://wawa.test"
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
POSTER = "https://image.tmdb.org/t/p/w500/serie-exemple.jpg"


@pytest.fixture(autouse=True)
def _catalog_sources_usable(app, client):
    del client
    connect_alldebrid_for_tests(app)


class _TmdbStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def search(self, title: str, media_type: str):
        self.calls.append((title, media_type))
        if title == "Serie Exemple":
            return [
                TmdbHit(
                    tmdb_id=9101,
                    media_type="series",
                    title="Serie Exemple",
                    year=2023,
                    overview=None,
                    poster_url=POSTER,
                )
            ]
        return []

    def genre_names(self, media_type: str):
        return {}


def _db(app):
    return app.state.session_factory()


def _season_row(season: int) -> CatalogEntry:
    title = f"Serie Exemple - Saison {season}"
    return CatalogEntry(
        dedupe_key=f"serie exemple saison {season}||series|{season}|",
        title=title,
        title_folded=f"serie exemple saison {season}",
        year=None,
        media_type="series",
        season=season,
        source="wawacity",
        source_url=f"{BASE}/serie/exemple-saison-{season}",
        quality="HD",
        language="VFF",
        sources=[],
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


def _cache_count(db) -> int:
    return int(db.scalar(select(func.count()).select_from(CatalogTmdbCache)) or 0)


def _patch_tmdb(monkeypatch, stub: _TmdbStub) -> None:
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda *_args: "jeton-factice")
    monkeypatch.setattr("mediatracker.integrations.tmdb.shared_tmdb", lambda _token: stub)


def test_two_seasons_same_key_one_lookup_one_cache_row(client, app):
    del client
    db = _db(app)
    first, second = _season_row(1), _season_row(2)
    db.add_all([first, second])
    db.commit()
    assert tmdb_cache_key(first) == tmdb_cache_key(second)
    tmdb = _TmdbStub()
    resolve_catalog_posters(db, app.state.settings, [first, second], now=NOW, tmdb=tmdb, limit=5)
    db.commit()
    assert tmdb.calls == [("Serie Exemple", "series")]
    assert _cache_count(db) == 1
    assert first.poster_url == POSTER
    assert second.poster_url == POSTER
    assert first.tmdb_id == second.tmdb_id == 9101


def test_two_seasons_same_key_miss_one_cache_row(client, app):
    del client
    db = _db(app)
    rows = [_season_row(1), _season_row(2)]
    for row in rows:
        row.title = row.title.replace("Serie Exemple", "Serie Inconnue")
    db.add_all(rows)
    db.commit()
    tmdb = _TmdbStub()
    resolve_catalog_posters(db, app.state.settings, rows, now=NOW, tmdb=tmdb, limit=5)
    db.commit()
    assert len(tmdb.calls) == 1
    assert _cache_count(db) == 1
    assert all(row.poster_url is None for row in rows)


def test_write_cache_twice_same_key_reuses_pending_row(client, app):
    del client
    db = _db(app)
    key = "v2|series||serie exemple"
    _write_cache(db, key, NOW, found=False, tmdb_id=None, poster_url=None)
    _write_cache(db, key, NOW, found=True, tmdb_id=9101, poster_url=POSTER)
    db.commit()
    assert _cache_count(db) == 1
    row = db.get(CatalogTmdbCache, key)
    assert row.found is True
    assert row.poster_url == POSTER


def test_list_catalog_two_seasons_same_key(client, app, monkeypatch):
    db = _db(app)
    db.add_all([_season_row(1), _season_row(2)])
    db.commit()
    tmdb = _TmdbStub()
    _patch_tmdb(monkeypatch, tmdb)
    login(client)
    response = client.get("/api/v1/catalog?type=series", headers=csrf(client))
    assert response.status_code == 200, response.text
    assert len(tmdb.calls) == 1
    assert _cache_count(_db(app)) == 1
    posters = {item["title"]: item["poster_url"] for item in response.json()["items"]}
    assert posters["Serie Exemple - Saison 1"] == POSTER
    assert posters["Serie Exemple - Saison 2"] == POSTER


def test_refresh_route_two_seasons_advances_next_refresh(client, app, monkeypatch):
    db = _db(app)
    stale = datetime(2026, 10, 26, 18, 22, tzinfo=timezone.utc)
    db.add(Setting(key="catalog.next_refresh_at", value=stale.isoformat(), is_secret=False, updated_at=stale))
    db.commit()

    def _latest(media_type: str):
        if media_type != "series":
            return []
        return [
            ReleaseListing(
                title=f"Serie Exemple - Saison {season}",
                year=None,
                media_type="series",
                season=season,
                quality="HD",
                language="VFF",
                url=f"{BASE}/serie/exemple-saison-{season}",
                ref=f"{BASE}/serie/exemple-saison-{season}",
            )
            for season in (1, 2)
        ]

    feed = MagicMock()
    feed.capabilities = frozenset({"latest"})
    feed.latest_releases = _latest
    feed.close = lambda: None
    monkeypatch.setattr("mediatracker.api.v1.catalog.sources_for_catalog", lambda *_args: [(1, "wawacity", feed)])
    tmdb = _TmdbStub()
    _patch_tmdb(monkeypatch, tmdb)

    login(client)
    response = client.post("/api/v1/catalog/refresh", headers=csrf(client))
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 2
    assert len(tmdb.calls) == 1
    check = _db(app)
    _last, nxt, error = catalog_clock(check)
    assert error is None
    assert nxt is not None and nxt > stale + timedelta(hours=1)
    assert _cache_count(check) == 1
    posters = check.scalars(select(CatalogEntry.poster_url).where(CatalogEntry.media_type == "series")).all()
    assert sorted(posters) == [POSTER, POSTER]

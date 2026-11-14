"""Latest-releases catalog: parsing, dedupe, TMDB, follow, retention, and the shared pacer.

Fixtures stand in for the live listing pages. No test opens a socket.
"""

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from tests.conftest import connect_alldebrid_for_tests, csrf, login

from mediatracker.core.catalog import (
    fold_title,
    listing_kept,
    refresh_catalog,
)
from mediatracker.db.models import CatalogEntry, Event, LibraryFile, Media, WebhookDelivery
from mediatracker.integrations.tmdb import TmdbHit
from mediatracker.providers.base import CapabilityNotSupported, ReleaseListing, SourceFetchError
from mediatracker.providers.sources.loading import enabled_remote_sources, sources_for_catalog
from mediatracker.providers.sources.manual import ManualSource
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.providers.sources.wawacity_html import parse_listing

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
BASE = "https://wawa.test"


@pytest.fixture(autouse=True)
def _catalog_sources_usable(app, client):
    del client
    connect_alldebrid_for_tests(app)


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class _Feed:
    def __init__(self, rows: dict[str, list[ReleaseListing]], *, fail: str | None = None) -> None:
        self.capabilities = frozenset({"latest"})
        self.rows = rows
        self.fail = fail
        self.calls: list[str] = []

    def latest_releases(self, media_type: str) -> list[ReleaseListing]:
        self.calls.append(media_type)
        if self.fail:
            raise SourceFetchError(self.fail, "Wawacity a renvoyé un contrôle Cloudflare.", challenge=True)
        return list(self.rows.get(media_type, []))

    def close(self) -> None:
        return None


def _listing(**kwargs) -> ReleaseListing:
    payload = {
        "title": "Archipel Pilote",
        "year": 2021,
        "media_type": "film",
        "quality": "HD",
        "language": "VFF",
        "url": "https://wawa.test/film/archipel-pilote",
        "ref": "https://wawa.test/film/archipel-pilote",
    }
    payload.update(kwargs)
    return ReleaseListing(**payload)


def _db(app):
    return app.state.session_factory()


def test_parse_latest_films_and_series():
    films = parse_listing(_text("latest_films.html"), BASE, "film")
    by_url = {hit.url: hit for hit in films}
    archipel = by_url[f"{BASE}/film/archipel-pilote-2021-hd-vff"]
    assert archipel.title == "Archipel Pilote"
    assert archipel.year == 2021
    assert archipel.quality == "HD"
    assert archipel.language == "VFF"
    assert archipel.media_type == "film"
    assert by_url[f"{BASE}/film/archipel-pilote-2021-1080p-multi"].quality == "1080p"
    assert by_url[f"{BASE}/film/mimosa-demo-hd-vf"].title == "Mimosa Démo"
    assert by_url[f"{BASE}/film/limite-demo-4k-vff"].quality == "4K"

    series = parse_listing(_text("latest_series.html"), BASE, "series")
    titled = {hit.title: hit for hit in series}
    assert titled["Orbite Démo"].season == 2
    assert titled["Orbite Démo"].year == 2022
    assert titled["Orbite Démo"].language == "VFF"
    assert fold_title(titled["Palais Démo"].title) == "palais demo"
    assert titled["Palais Démo"].season == 1
    boys = titled["Équipe Démo"]
    assert boys.season == 1
    assert boys.episode == 5
    assert titled["Brume Série Démo"].language == "VOSTFR"
    assert all(hit.media_type == "series" for hit in series)


def test_wawacity_latest_uses_listing_pages_and_the_pacer():
    clock = _Clock()
    pacer = SourcePacer(5, clock=clock, sleeper=clock.sleep, rng=random.Random(0))
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers["user-agent"] == "TestAgent/1.0"
        if "p=films" in str(request.url):
            return httpx.Response(200, text=_text("latest_films.html"))
        if "p=series" in str(request.url):
            return httpx.Response(200, text=_text("latest_series.html"))
        return httpx.Response(404, text="absent")

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=pacer,
    )
    films = source.latest_releases("film")
    series = source.latest_releases("series")
    source.close()
    assert calls[0].endswith("/?p=films&s=blu-ray_1080p-720p")
    assert calls[1].endswith("/?p=series&s=vf-hq")
    assert any(item.title == "Archipel Pilote" and item.year == 2021 for item in films)
    assert any(item.title == "Orbite Démo" and item.season == 2 for item in series)
    assert pacer.waits[0] == 0
    assert pacer.waits[1] >= 5


def test_manual_source_has_no_latest_feed():
    try:
        ManualSource().latest_releases("film")
    except CapabilityNotSupported as exc:
        assert exc.capability == "latest"
    else:
        raise AssertionError("la source manuelle ne publie pas de nouveautés")


def test_listing_filter_defaults_to_hd_vff():
    archipel_listing = _listing()
    other = _listing(quality="1080p", language="MULTI", url="https://wawa.test/other", ref="https://wawa.test/other")
    assert listing_kept(archipel_listing, "HD", "VFF")
    assert not listing_kept(other, "HD", "VFF")
    assert listing_kept(other, None, None)


def test_dedupe_across_sources_and_hd_vff_filter(client, app):
    del client
    first = _Feed(
        {
            "film": [
                _listing(),
                _listing(quality="1080p", language="MULTI", url="https://wawa.test/multi", ref="https://wawa.test/multi"),
                _listing(title="Zorblax", year=2024, url="https://wawa.test/z", ref="https://wawa.test/z"),
            ],
            "series": [],
        }
    )
    second = _Feed(
        {
            "film": [_listing(url="https://other.test/archipel-pilote", ref="https://other.test/archipel-pilote")],
            "series": [],
        }
    )
    db = _db(app)
    try:
        report = refresh_catalog(
            db,
            app.state.settings,
            now=NOW,
            sources=[(1, "wawacity", first), (2, "other", second)],
            force=True,
        )
        assert report.added == 2
        assert report.error is None
        rows = list(db.query(CatalogEntry).order_by(CatalogEntry.title).all())
        assert [row.title for row in rows] == ["Archipel Pilote", "Zorblax"]
        archipel_row = rows[0]
        assert {item["source"] for item in archipel_row.sources} == {"wawacity", "other"}
        assert len(archipel_row.sources) == 2
        assert archipel_row.first_seen_at <= archipel_row.last_seen_at
    finally:
        db.close()


def test_tmdb_match_keeps_unmatched_rows(client, app):
    del client
    class FakeTmdb:
        def search(self, query: str, media_type: str):
            assert media_type == "film"
            if query == "Archipel Pilote":
                return [
                    TmdbHit(
                        900005,
                        "film",
                        "Archipel Pilote",
                        2021,
                        "Désert.",
                        "https://image.tmdb.org/t/p/w500/archipel-demo.jpg",
                        rating=7.9,
                        genre_ids=(878, 12),
                    )
                ]
            return []

        def genre_names(self, media_type: str) -> dict[int, str]:
            assert media_type == "film"
            return {878: "Science-Fiction", 12: "Aventure"}

        def close(self) -> None:
            return None

    feed = _Feed(
        {
            "film": [_listing(), _listing(title="Zorblax", year=2024, url="https://wawa.test/z", ref="z")],
            "series": [],
        }
    )
    db = _db(app)
    try:
        report = refresh_catalog(
            db,
            app.state.settings,
            now=NOW,
            sources=[(1, "wawacity", feed)],
            tmdb=FakeTmdb(),
            force=True,
        )
        assert report.matched == 1
        archipel_entry = db.query(CatalogEntry).filter(CatalogEntry.title == "Archipel Pilote").one()
        assert archipel_entry.tmdb_id == 900005
        assert archipel_entry.overview == "Désert."
        assert archipel_entry.poster_url.endswith("archipel-demo.jpg")
        assert archipel_entry.rating == 7.9
        assert archipel_entry.genres == ["Science-Fiction", "Aventure"]
        zorblax = db.query(CatalogEntry).filter(CatalogEntry.title == "Zorblax").one()
        assert zorblax.tmdb_id is None
        assert zorblax.tmdb_checked_at is not None
    finally:
        db.close()


def test_follow_from_catalog_sets_series_policy(client, app):
    feed = _Feed(
        {
            "film": [_listing(title="Archipel Pilote", year=2021)],
            "series": [
                _listing(
                    title="Orbite Démo",
                    year=2022,
                    media_type="series",
                    season=2,
                    url="https://wawa.test/orbite-demo",
                    ref="https://wawa.test/orbite-demo",
                )
            ],
        }
    )
    db = _db(app)
    try:
        refresh_catalog(db, app.state.settings, now=NOW, sources=[(1, "wawacity", feed)], force=True)
        series = db.query(CatalogEntry).filter(CatalogEntry.title == "Orbite Démo").one()
        film = db.query(CatalogEntry).filter(CatalogEntry.title == "Archipel Pilote").one()
        series_id, film_id = series.id, film.id
    finally:
        db.close()

    login(client)
    missing = client.post(f"/api/v1/catalog/{series_id}/follow", json={}, headers=csrf(client))
    assert missing.status_code == 422

    created = client.post(
        f"/api/v1/catalog/{series_id}/follow",
        json={"new_episode_policy": "auto"},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["title"] == "Orbite Démo"
    assert body["type"] == "series"
    assert body["year"] == 2022
    assert body["new_episode_policy"] == "auto"
    assert body["auto_download"] is True

    again = client.post(
        f"/api/v1/catalog/{series_id}/follow",
        json={"new_episode_policy": "propose"},
        headers=csrf(client),
    )
    assert again.status_code == 409

    film_follow = client.post(f"/api/v1/catalog/{film_id}/follow", json={}, headers=csrf(client))
    assert film_follow.status_code == 201, film_follow.text
    assert film_follow.json()["type"] == "film"

    listed = client.get("/api/v1/catalog", params={"type": "series"})
    assert listed.status_code == 200, listed.text
    card = listed.json()["items"][0]
    assert card["followed"] is True
    assert card["season"] == 2


def test_follow_stores_seasons_and_can_start_now(client, app):
    feed = _Feed(
        {
            "film": [],
            "series": [
                _listing(
                    title="Orbite Démo",
                    year=2022,
                    media_type="series",
                    season=1,
                    url="https://wawa.test/orbite-demo",
                    ref="https://wawa.test/orbite-demo",
                ),
                _listing(
                    title="Autres",
                    year=2024,
                    media_type="series",
                    season=1,
                    url="https://wawa.test/autres",
                    ref="https://wawa.test/autres",
                ),
            ],
        }
    )
    db = _db(app)
    try:
        refresh_catalog(db, app.state.settings, now=NOW, sources=[(1, "wawacity", feed)], force=True)
        andor = db.query(CatalogEntry).filter(CatalogEntry.title == "Orbite Démo").one()
        autres = db.query(CatalogEntry).filter(CatalogEntry.title == "Autres").one()
        andor_id, autres_id = andor.id, autres.id
    finally:
        db.close()

    login(client)
    created = client.post(
        f"/api/v1/catalog/{andor_id}/follow",
        json={"new_episode_policy": "propose", "seasons": [2, 1, 1], "start_now": True},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    assert created.json()["new_episode_policy"] == "propose"
    db = _db(app)
    try:
        media = db.query(Media).filter(Media.title == "Orbite Démo").one()
        assert media.tracked_seasons == [1, 2]
        assert media.download_backlog is True
        assert media.catch_up_pending is True
    finally:
        db.close()

    refused = client.post(
        f"/api/v1/catalog/{autres_id}/follow",
        json={"new_episode_policy": "auto", "seasons": [99], "start_now": False},
        headers=csrf(client),
    )
    assert refused.status_code == 422

    quiet = client.post(
        f"/api/v1/catalog/{autres_id}/follow",
        json={"new_episode_policy": "auto", "start_now": False},
        headers=csrf(client),
    )
    assert quiet.status_code == 201, quiet.text
    db = _db(app)
    try:
        media = db.query(Media).filter(Media.title == "Autres").one()
        assert media.tracked_seasons is None
        assert media.download_backlog is False
        assert media.catch_up_pending is False
    finally:
        db.close()


def test_library_badge_and_ignore(client, app):
    feed = _Feed({"film": [_listing()], "series": []})
    db = _db(app)
    try:
        refresh_catalog(db, app.state.settings, now=NOW, sources=[(1, "wawacity", feed)], force=True)
        entry = db.query(CatalogEntry).one()
        db.add(
            LibraryFile(
                path="/data/media/Films/Archipel Pilote (2021)/Archipel Pilote.mkv",
                size=10,
                parsed_title="Archipel Pilote",
                parsed_year=2021,
                media_type="film",
                status="matched",
                seen_at=NOW,
            )
        )
        db.commit()
        entry_id = entry.id
    finally:
        db.close()

    login(client)
    page = client.get("/api/v1/catalog", params={"type": "film", "in_library": "true"})
    assert page.status_code == 200, page.text
    assert page.json()["items"][0]["in_library"] is True
    absent = client.get("/api/v1/catalog", params={"type": "film", "in_library": "false"})
    assert absent.json()["total"] == 0

    hidden = client.post(f"/api/v1/catalog/{entry_id}/ignore", headers=csrf(client))
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["ignored"] is True
    visible = client.get("/api/v1/catalog", params={"type": "film"})
    assert visible.json()["total"] == 0
    shown = client.get("/api/v1/catalog", params={"type": "film", "include_ignored": "true"})
    assert shown.json()["total"] == 1


def test_retention_purges_rows_last_seen_before_the_window(client, app):
    del client
    feed = _Feed(
        {
            "film": [_listing(title="Récent", year=2026, url="https://wawa.test/recent", ref="recent")],
            "series": [],
        }
    )
    db = _db(app)
    try:
        db.add(
            CatalogEntry(
                dedupe_key="ancien|1990|film||",
                title="Ancien",
                year=1990,
                media_type="film",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://wawa.test/ancien",
                sources=[{"source": "wawacity", "url": "https://wawa.test/ancien"}],
                first_seen_at=NOW - timedelta(days=90),
                last_seen_at=NOW - timedelta(days=90),
                genres=[],
            )
        )
        db.commit()
        report = refresh_catalog(
            db,
            app.state.settings,
            now=NOW,
            sources=[(1, "wawacity", feed)],
            force=True,
        )
        assert report.purged == 1
        titles = {row.title for row in db.query(CatalogEntry).all()}
        assert titles == {"Récent"}
    finally:
        db.close()


def test_refresh_backoff_reuses_the_source_delay(client, app):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"catalog_interval_hours": 1, "source_error_backoff_min": 180}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    feed = _Feed({}, fail="CLOUDFLARE")
    db = _db(app)
    try:
        report = refresh_catalog(
            db,
            app.state.settings,
            now=NOW,
            sources=[(1, "wawacity", feed)],
            force=True,
        )
        assert report.error
        assert report.next_refresh_at is not None
        assert report.refreshed_at is not None
        gap = report.next_refresh_at - report.refreshed_at
        assert gap == timedelta(minutes=180)
        skipped = refresh_catalog(
            db,
            app.state.settings,
            now=NOW + timedelta(minutes=30),
            sources=[(1, "wawacity", feed)],
            force=False,
        )
        assert skipped.skipped is True
        assert feed.calls == ["film", "series"]
    finally:
        db.close()


def test_nouveautes_digest_is_one_event_and_off_unless_subscribed(client, app):
    login(client)
    catalog = client.get("/api/v1/events/notifications")
    assert catalog.status_code == 200
    row = next(item for item in catalog.json()["events"] if item["type"] == "nouveautes")
    assert row["label"] == "Nouveautés"
    assert row["default_on"] is False
    passage = next(item for item in catalog.json()["events"] if item["type"] == "cleanup.pass")
    assert passage["label"] == "Passage du nettoyage"
    assert passage["default_on"] is False
    quiet = {"nouveautes", "cleanup.pass"}
    assert all(item["default_on"] for item in catalog.json()["events"] if item["type"] not in quiet)

    created = client.post(
        "/api/v1/webhooks",
        json={
            "name": "Digest",
            "type": "generic",
            "url": "https://example.test/hook",
            "secret": "digest-secret",
            "events": ["nouveautes"],
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text

    feed = _Feed(
        {
            "film": [
                _listing(),
                _listing(
                    title="Grille Démo",
                    year=1999,
                    url="https://wawa.test/grille-demo",
                    ref="grille-demo",
                ),
            ],
            "series": [],
        }
    )
    db = _db(app)
    try:
        refresh_catalog(db, app.state.settings, now=NOW, sources=[(1, "wawacity", feed)], force=True)
        events = list(db.query(Event).filter(Event.type == "nouveautes").all())
        assert len(events) == 1
        assert events[0].payload["grouped"] is True
        assert events[0].payload["count"] == 2
        assert {item["title"] for item in events[0].payload["items"]} == {"Archipel Pilote", "Grille Démo"}
        deliveries = list(db.query(WebhookDelivery).all())
        assert len(deliveries) == 1
        assert deliveries[0].event_id == events[0].id

        again = refresh_catalog(
            db,
            app.state.settings,
            now=NOW + timedelta(hours=13),
            sources=[(1, "wawacity", feed)],
            force=True,
        )
        assert again.added == 0
        assert db.query(Event).filter(Event.type == "nouveautes").count() == 1
    finally:
        db.close()


def test_catalog_reuses_the_poll_pacer(client, app):
    from tests.conftest import connect_alldebrid_for_tests

    login(client)
    connect_alldebrid_for_tests(app)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    import mediatracker.providers.sources.loading as loading

    loading._PACER = None
    loading._PACER_DELAY = None
    loading._CACHE = None
    loading._CACHE_TTL = None
    db = _db(app)
    try:
        poll = enabled_remote_sources(db, app.state.settings)
        catalog = sources_for_catalog(db, app.state.settings)
        assert poll and catalog
        assert poll[0][1].pacer is catalog[0][2].pacer
        assert poll[0][1].cache is catalog[0][2].cache
        assert "latest" in catalog[0][2].capabilities
        poll[0][1].close()
        catalog[0][2].close()
    finally:
        db.close()
        loading._PACER = None
        loading._CACHE = None


def test_second_refresh_is_not_due_until_the_interval(client, app):
    del client
    feed = _Feed({"film": [_listing()], "series": []})
    db = _db(app)
    try:
        first = refresh_catalog(db, app.state.settings, now=NOW, sources=[(1, "wawacity", feed)], force=True)
        assert first.added == 1
        second = refresh_catalog(
            db,
            app.state.settings,
            now=NOW + timedelta(hours=1),
            sources=[(1, "wawacity", feed)],
            force=False,
        )
        assert second.skipped is True
        assert feed.calls == ["film", "series"]
    finally:
        db.close()


def test_search_genre_and_sort(client, app):
    db = _db(app)
    try:
        older = CatalogEntry(
            dedupe_key="grille-demo|1999|film||",
            title="Grille Démo",
            year=1999,
            media_type="film",
            quality="HD",
            language="VFF",
            source="wawacity",
            source_url="https://wawa.test/grille-demo",
            sources=[],
            first_seen_at=NOW - timedelta(days=2),
            last_seen_at=NOW,
            tmdb_id=901003,
            poster_url="https://image.tmdb.org/t/p/w500/grille-demo.jpg",
            overview="Simulation.",
            rating=8.2,
            genres=["Science-Fiction"],
        )
        newer = CatalogEntry(
            dedupe_key="archipel-pilote|2021|film||",
            title="Archipel Pilote",
            year=2021,
            media_type="film",
            quality="HD",
            language="VFF",
            source="wawacity",
            source_url="https://wawa.test/archipel-pilote",
            sources=[],
            first_seen_at=NOW,
            last_seen_at=NOW,
            genres=["Aventure"],
        )
        db.add(older)
        db.add(newer)
        db.add(Media(type="film", title="Grille Démo", year=1999, tmdb_id=901003, followed=True, created_at=NOW))
        db.commit()
    finally:
        db.close()

    login(client)
    page = client.get("/api/v1/catalog", params={"type": "film", "q": "grille"})
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["total"] == 1
    assert body["items"][0]["followed"] is True
    assert body["items"][0]["tmdb_url"] == "https://www.themoviedb.org/movie/901003"
    genre = client.get("/api/v1/catalog", params={"type": "film", "genre": "Aventure", "year": 2021})
    assert [item["title"] for item in genre.json()["items"]] == ["Archipel Pilote"]
    ordered = client.get("/api/v1/catalog", params={"type": "film"})
    assert [item["title"] for item in ordered.json()["items"]] == ["Archipel Pilote", "Grille Démo"]


def test_search_q_treats_underscore_as_literal(client, app):
    db = _db(app)
    try:
        db.add(
            CatalogEntry(
                dedupe_key="abcde|2020|film||",
                title="abcde",
                year=2020,
                media_type="film",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://wawa.test/abcde",
                sources=[],
                first_seen_at=NOW,
                last_seen_at=NOW,
                genres=[],
            )
        )
        db.add(
            CatalogEntry(
                dedupe_key="abcxde|2020|film||",
                title="abcXde",
                year=2020,
                media_type="film",
                quality="HD",
                language="VFF",
                source="wawacity",
                source_url="https://wawa.test/abcxde",
                sources=[],
                first_seen_at=NOW - timedelta(hours=1),
                last_seen_at=NOW,
                genres=[],
            )
        )
        db.commit()
    finally:
        db.close()

    login(client)
    page = client.get("/api/v1/catalog", params={"type": "film", "q": "abc_de"})
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 0

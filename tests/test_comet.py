"""Comet is a download source only. Wawacity stays the catalog and the default hoster path."""

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, login
from tests.test_pipeline import FakeDebrid, FakeDownloader, _enable_debrid, _link, _series

from mediatracker.core.magnet_claim import claim_cached_magnet
from mediatracker.core.pipeline import tick
from mediatracker.core.selection import parse_download_source_order
from mediatracker.db.models import Download, Link, Media
from mediatracker.integrations.tmdb import TmdbClient
from mediatracker.providers.base import DirectLink, EpisodeQuery, LinkCandidate, SourcePreferences
from mediatracker.providers.debrid.alldebrid import AllDebridClient, AllDebridError, MagnetFile, MagnetSnapshot
from mediatracker.providers.sources.comet import CometPathCache, CometSource, keyless_config_token, select_streams
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer

FIXTURES = Path(__file__).parent / "fixtures" / "comet"
SERIES_DEMO = EpisodeQuery(
    title="Clinique Démo", year=2025, media_type="series", imdb_id="tt0000002", season=1, episode=1
)
DEMO_SUITE_QUERY = EpisodeQuery(
    title="Suite Ville Démo",
    year=2021,
    media_type="series",
    imdb_id="tt9000002",
    season=3,
    episode=4,
)
PREFS = SourcePreferences(quality="HD", language="VFF", allow_fallback=False)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


PACK_QUERY = EpisodeQuery(
    title="Alpha",
    year=2020,
    media_type="series",
    imdb_id="tt0000099",
    season=1,
    episode=5,
)
PACK_PREFS = SourcePreferences(quality="HD", language="MULTI", allow_fallback=True)


def test_season_pack_streams_require_file_idx():
    payload = {
        "streams": [
            {
                "infoHash": "b" * 40,
                "behaviorHints": {"filename": "Alpha.S01.Complete.1080p.MULTi.mkv"},
            },
            {
                "infoHash": "c" * 40,
                "fileIdx": 2,
                "behaviorHints": {"filename": "Alpha.S01.Complete.1080p.MULTi.mkv"},
            },
            {
                "infoHash": "a" * 40,
                "behaviorHints": {"filename": "Alpha.S01E05.1080p.MULTi.mkv"},
            },
        ]
    }
    chosen = select_streams(payload, PACK_QUERY, PACK_PREFS)
    ids = {item.external_id for item in chosen}
    assert f"comet:{'a' * 40}:0" in ids
    assert f"comet:{'c' * 40}:2" in ids
    assert f"comet:{'b' * 40}:0" not in ids


def test_demo_hospital_fixture_keeps_french_1080p_and_drops_the_rest():
    chosen = select_streams(_load("demo_hospital_s01e01.json"), SERIES_DEMO, PREFS)
    assert [item.filename for item in chosen] == [
        "Clinique.Demo.S01E01.1080p.WEB.H264.MULTi.mkv",
        "Clinique.Demo.S01E01.720p.WEB.H264.VF.mkv",
    ]
    assert chosen[0].url == "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert chosen[0].file_index == 0
    assert chosen[0].quality == "1080p"
    assert chosen[0].language == "MULTI"
    assert chosen[0].size == 1503238553
    assert all("1fichier" not in item.url for item in chosen)


def test_demo_suite_fixture_prefers_1080p_french():
    chosen = select_streams(_load("demo_suite_s03e04.json"), DEMO_SUITE_QUERY, PREFS)
    assert [item.language for item in chosen] == ["VFF", "VFF", "VF"]
    assert chosen[0].quality == "1080p"
    assert chosen[0].filename.startswith("Suite.Ville.Démo.S03E04.")
    assert all("SCREENER" not in (item.filename or "") for item in chosen)
    assert all("HDTS" not in (item.filename or "") for item in chosen)


def test_vostfr_and_4k_are_opt_in():
    payload = _load("demo_hospital_s01e01.json")
    vost = select_streams(payload, SERIES_DEMO, SourcePreferences(quality="HD", language="VOSTFR"))
    assert [item.language for item in vost] == ["VOSTFR"]
    ultra = select_streams(
        payload,
        SERIES_DEMO,
        SourcePreferences(quality="4K", language="VFF"),
    )
    assert ultra[0].quality == "4K"
    fallback = select_streams(
        payload,
        SERIES_DEMO,
        SourcePreferences(quality="HD", language="VFF", allow_fallback=True, quality_fallback=["4K"]),
    )
    assert any(item.quality == "4K" for item in fallback)


def test_keyless_url_is_cached_and_a_404_uses_the_config_segment():
    fixture_body = json.dumps(_load("demo_hospital_s01e01.json"))
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.startswith("/stream/"):
            return httpx.Response(404)
        assert keyless_config_token() in request.url.path
        return httpx.Response(200, text=fixture_body)

    cache = CometPathCache(revalidate_seconds=3600.0)
    cache.bind_domain("https://comet.feels.legal")
    source = CometSource(
        "https://comet.feels.legal",
        transport=httpx.MockTransport(handler),
        cache=cache,
        pacer=SourcePacer(0),
    )
    try:
        first = source.links_for(SERIES_DEMO, PREFS)
        second = source.links_for(SERIES_DEMO, PREFS)
    finally:
        source.close()
    assert first[0].filename.startswith("Clinique.Demo.S01E01.1080p")
    assert second[0].external_id == first[0].external_id
    assert len(seen) == 2
    assert seen[0] == "/stream/series/tt0000002:1:1.json"
    assert seen[1].endswith("/stream/series/tt0000002:1:1.json")


def test_movie_path_and_empty_imdb_are_quiet():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"streams": []})

    source = CometSource(
        "https://comet.example",
        transport=httpx.MockTransport(handler),
        cache=HtmlCache(0),
        pacer=SourcePacer(0),
    )
    try:
        film = EpisodeQuery(title="Archipel Pilote", media_type="film", imdb_id="tt0000001")
        assert source.links_for(film, PREFS) == []
        assert source.links_for(EpisodeQuery(title="Archipel Pilote", media_type="film"), PREFS) == []
    finally:
        source.close()
    assert seen == ["/stream/movie/tt0000001.json"]


def test_tmdb_external_ids_store_the_imdb_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/tv/900122/external_ids")
        return httpx.Response(200, json={"imdb_id": "tt0000002", "tvdb_id": 1})

    client = TmdbClient("token", transport=httpx.MockTransport(handler))
    try:
        assert client.external_imdb(900122, "series") == "tt0000002"
    finally:
        client.close()


def test_source_order_defaults_to_wawacity_then_comet():
    assert parse_download_source_order("wawacity, comet") == ["wawacity", "comet"]
    assert parse_download_source_order("comet,wawacity") == ["comet", "wawacity"]
    assert parse_download_source_order("wawacity, torrentio") == ["wawacity", "torrentio"]


def test_settings_and_fiche_accept_the_source_order(client):
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "comet, wawacity", "comet_base_url": "https://comet.example"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text
    rows = {item["key"]: item["value"] for item in saved.json()["settings"]}
    assert rows["download_source_order"] == "comet,wawacity"
    assert rows["comet_base_url"] == "https://comet.example"
    refused = client.patch(
        "/api/v1/settings",
        json={"values": {"download_source_order": "wawacity,1fichier"}},
        headers=csrf(client),
    )
    assert refused.status_code == 422
    media = _series(client)
    patched = client.patch(
        f"/api/v1/media/{media['id']}",
        json={"source_order": "comet, wawacity"},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["source_order"] == "comet,wawacity"
    cleared = client.patch(
        f"/api/v1/media/{media['id']}",
        json={"source_order": ""},
        headers=csrf(client),
    )
    assert cleared.status_code == 200
    assert cleared.json()["source_order"] is None


def test_uncached_magnet_is_deleted_and_never_uploaded_when_instant_says_no():
    class _Debrid:
        def __init__(self) -> None:
            self.uploaded: list[str] = []
            self.deleted: list[str] = []
            self.cached = False

        def magnet_cache(self, magnets: list[str]):
            self.magnets = magnets
            return [type("Hit", (), {"magnet": magnets[0], "cached": self.cached})()]

        def upload_magnet(self, magnet: str) -> MagnetSnapshot:
            self.uploaded.append(magnet)
            return MagnetSnapshot("9", False, [])

        def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
            return MagnetSnapshot(magnet_id, False, [])

        def delete_magnet(self, magnet_id: str) -> None:
            self.deleted.append(magnet_id)

        def unlock(self, url: str) -> DirectLink:
            raise AssertionError(url)

    cold = _Debrid()
    assert claim_cached_magnet(cold, "magnet:?xt=urn:btih:abc", file_index=0, filename="a.mkv", wait_seconds=0) is None
    assert cold.uploaded == []
    assert cold.deleted == []

    cold.cached = True
    result = claim_cached_magnet(
        cold,
        "magnet:?xt=urn:btih:abc",
        file_index=0,
        filename="a.mkv",
        wait_seconds=0,
        blocking_wait=True,
    )
    assert result is None
    assert cold.uploaded == ["magnet:?xt=urn:btih:abc"]
    assert cold.deleted == ["9"]


def test_cached_magnet_unlocks_the_named_file_and_skips_uptobox_inside_torrent():
    class _Debrid:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def magnet_cache(self, magnets: list[str]):
            return [type("Hit", (), {"magnet": magnets[0], "cached": True})()]

        def upload_magnet(self, magnet: str) -> MagnetSnapshot:
            return MagnetSnapshot(
                "4",
                True,
                [
                    MagnetFile(0, "sample.mkv", 10, "https://uptobox.com/abc"),
                    MagnetFile(1, "Clinique.Demo.S01E01.1080p.WEB.H264.MULTi.mkv", 20, "https://alldebrid.com/f/ok"),
                ],
            )

        def delete_magnet(self, magnet_id: str) -> None:
            self.deleted.append(magnet_id)

        def unlock(self, url: str) -> DirectLink:
            assert "uptobox" not in url
            return DirectLink(url="https://cdn.example/comet.mkv", filename="Clinique.Demo.S01E01.mkv", size=20)

    debrid = _Debrid()
    claimed = claim_cached_magnet(
        debrid,
        "magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        file_index=0,
        filename="Clinique.Demo.S01E01.1080p.WEB.H264.MULTi.mkv",
        wait_seconds=0,
    )
    assert claimed is not None
    assert claimed.magnet_id == "4"
    assert claimed.direct.url == "https://cdn.example/comet.mkv"
    assert debrid.deleted == []


def test_alldebrid_upload_parses_a_ready_magnet():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v4/magnet/upload")
        assert b"btih" in request.content
        return httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "magnets": [
                        {
                            "id": 15,
                            "ready": True,
                            "files": [{"n": "Show.S01E01.mkv", "s": 30, "l": "https://alldebrid.com/f/xyz"}],
                        }
                    ]
                },
            },
        )

    client_ad = AllDebridClient("test-key", transport=httpx.MockTransport(handler), sleeper=lambda _s: None)
    snapshot = client_ad.upload_magnet("magnet:?xt=urn:btih:abc")
    assert snapshot.id == "15"
    assert snapshot.ready is True
    assert snapshot.files[0].link.endswith("/xyz")


class _CacheDebrid(FakeDebrid):
    def __init__(self, *, cached: bool = True, ready: bool = True) -> None:
        super().__init__()
        self.cached = cached
        self.ready = ready
        self.uploaded: list[str] = []
        self.deleted: list[str] = []
        self.cache_wait_seconds = 0.0

    def magnet_cache(self, magnets: list[str]):
        return [type("Hit", (), {"magnet": magnets[0], "cached": self.cached, "name": "Show"})()]

    def upload_magnet(self, magnet: str) -> MagnetSnapshot:
        self.uploaded.append(magnet)
        files = [
            MagnetFile(0, "Clinique.Demo.S01E01.1080p.WEB.H264.MULTi.mkv", 40, "https://alldebrid.com/f/demo")
        ]
        return MagnetSnapshot("77", self.ready, files if self.ready else [])

    def magnet_status(self, magnet_id: str) -> MagnetSnapshot:
        return MagnetSnapshot(magnet_id, False, [])

    def delete_magnet(self, magnet_id: str) -> None:
        self.deleted.append(magnet_id)

    def unlock(self, url: str) -> DirectLink:
        if url.startswith("https://alldebrid.com/"):
            return DirectLink(url="https://cdn.example/comet.mkv", filename="Clinique.Demo.S01E01.mkv", size=40)
        return super().unlock(url)


def _series_demo(client, app, *, order: str | None = None) -> dict:
    media = _series(client)
    db = app.state.session_factory()
    try:
        row = db.get(Media, media["id"])
        row.title = "Clinique Démo"
        row.imdb_id = "tt0000002"
        row.source_order = order
        db.commit()
    finally:
        db.close()
    return media


def _stub_source(monkeypatch):
    candidate = LinkCandidate(
        url="magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        kind="magnet",
        quality="1080p",
        language="MULTI",
        size=PLAUSIBLE_VIDEO_BYTES,
        filename="Clinique.Demo.S01E01.1080p.WEB.H264.MULTi.mkv",
        season=1,
        episode=1,
        file_index=0,
        external_id="comet:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:0",
    )

    class _Source:
        type = "comet"
        calls = 0

        def links_for(self, query, preferences):
            del preferences
            assert query.imdb_id == "tt0000002"
            assert query.season == 1 and query.episode == 1
            self.calls += 1
            return [candidate]

        def close(self) -> None:
            return None

    source = _Source()

    def opener(name, settings, *, db=None, base_url=None, base_urls=None, transport=None):
        del settings, db, base_url, base_urls, transport
        assert name == "comet"
        return source

    monkeypatch.setattr("mediatracker.providers.sources.loading.episode_source", opener)
    return source


def test_wawacity_hoster_is_used_before_comet(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    source = _stub_source(monkeypatch)
    debrid = _CacheDebrid()
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=downloader,
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
    finally:
        db.close()
    assert source.calls == 0
    assert debrid.uploaded == []
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["source"] == "Manuel (rapidgator)"


def test_dead_hoster_falls_back_to_a_cached_comet_magnet(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app)
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _CacheDebrid()
    debrid.mode = "dead"
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=downloader,
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.debrid_magnet_id == "77"
    finally:
        db.close()
    assert debrid.uploaded == ["magnet:?xt=urn:btih:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert debrid.deleted == []
    assert downloader.urls == ["https://cdn.example/comet.mkv"]
    listed = client.get("/api/v1/downloads").json()["items"][0]
    assert listed["source"] == "Comet"
    assert "magnet:" not in "".join(downloader.urls)


def test_comet_can_be_switched_to_primary_on_the_fiche(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet,wawacity")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _CacheDebrid()
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=downloader,
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.debrid_magnet_id == "77"
    finally:
        db.close()
    assert debrid.seen == []
    assert downloader.urls == ["https://cdn.example/comet.mkv"]


def test_uncached_comet_falls_through_to_the_hoster(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet,wawacity")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    _stub_source(monkeypatch)
    debrid = _CacheDebrid(cached=False)
    downloader = FakeDownloader()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=downloader,
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.debrid_magnet_id is None
    finally:
        db.close()
    assert debrid.uploaded == []
    assert debrid.deleted == []
    assert downloader.urls == ["https://cdn.example/secret.mkv"]


class _DiscontinuedDebrid(_CacheDebrid):
    """AllDebrid rejects one magnet forever and accepts the rest."""

    def __init__(self, *args, bad: str = "a" * 40, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.bad = bad

    def upload_magnet(self, magnet: str) -> MagnetSnapshot:
        if self.bad in magnet:
            self.uploaded.append(magnet)
            raise AllDebridError("DISCONTINUED", "This magnet is discontinued", retryable=False)
        return super().upload_magnet(magnet)


def _candidates(*idents: str) -> list[LinkCandidate]:
    found = []
    for ident in idents:
        found.append(
            LinkCandidate(
                url=f"magnet:?xt=urn:btih:{ident}",
                kind="magnet",
                quality="1080p",
                language="MULTI",
                size=PLAUSIBLE_VIDEO_BYTES,
                filename="Clinique.Demo.S01E01.1080p.WEB.H264.MULTi.mkv",
                season=1,
                episode=1,
                file_index=0,
                external_id=f"comet:{ident}:0",
            )
        )
    return found


def _patch_candidates(monkeypatch, candidates: list[LinkCandidate]):
    class _Source:
        type = "comet"

        def links_for(self, query, preferences):
            del query, preferences
            return list(candidates)

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.episode_source",
        lambda name, settings, *, db=None, base_url=None, base_urls=None, transport=None: _Source(),
    )


def test_discontinued_magnet_is_not_uploaded_again(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    ident = "a" * 40
    _patch_candidates(monkeypatch, _candidates(ident))
    debrid = _DiscontinuedDebrid()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    db = app.state.session_factory()
    try:
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        row = db.get(Download, link["download_id"])
        assert row.state == "failed"
        assert row.error_code == "DISCONTINUED"
        assert "retiré" in (row.error or "")
        assert debrid.uploaded == [f"magnet:?xt=urn:btih:{ident}"]
        dead = db.query(Link).filter(Link.dead.is_(True)).all()
        assert dead and "DISCONTINUED" in (dead[0].last_error or "")
        # The failure commit can be lost when the database is locked. The magnet stays dead.
        row.state = "pending"
        row.next_retry_at = None
        row.finished_at = None
        row.error_code = None
        row.error = None
        db.commit()
        tick(db, app.state.settings, now=now, debrid=debrid, downloader=FakeDownloader())
        db.refresh(row)
        assert debrid.uploaded == [f"magnet:?xt=urn:btih:{ident}"]
        assert row.state == "failed"
        assert row.error_code == "DISCONTINUED"
    finally:
        db.close()


def test_discontinued_magnet_moves_on_to_the_next_candidate_and_source(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    good = "b" * 40
    bad = "a" * 40
    _patch_candidates(monkeypatch, _candidates(bad, good))
    debrid = _DiscontinuedDebrid()
    media = _series_demo(client, app, order="comet")
    link = _link(client, media["id"], "https://rapidgator.net/file/abc", 1)
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=FakeDownloader(),
        )
        row = db.get(Download, link["download_id"])
        assert row.state == "queued"
        assert row.debrid_magnet_id == "77"
    finally:
        db.close()
    assert debrid.uploaded == [f"magnet:?xt=urn:btih:{bad}", f"magnet:?xt=urn:btih:{good}"]


def test_discontinued_magnet_falls_through_to_the_next_source(client, app, monkeypatch):
    login(client)
    _enable_debrid(app)
    bad = "a" * 40
    media = _series_demo(client, app, order="comet,wawacity")
    hoster = _link(client, media["id"], "https://rapidgator.net/file/next", 1)
    _patch_candidates(monkeypatch, _candidates(bad))
    debrid = _DiscontinuedDebrid()
    db = app.state.session_factory()
    try:
        tick(
            db,
            app.state.settings,
            now=datetime(2026, 10, 12, tzinfo=timezone.utc),
            debrid=debrid,
            downloader=FakeDownloader(),
        )
        row = db.get(Download, hoster["download_id"])
        assert row.state == "queued"
        assert row.debrid_magnet_id is None
    finally:
        db.close()
    assert debrid.uploaded == [f"magnet:?xt=urn:btih:{bad}"]
    assert debrid.seen == ["https://rapidgator.net/file/next"]

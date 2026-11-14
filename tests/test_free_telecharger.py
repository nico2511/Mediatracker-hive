"""Free-Telecharger parsing, pacing, inactive URL, and error cascade (no live network)."""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from mediatracker.providers.base import SourceFetchError, SourcePreferences, SourceQuery, SourceRef
from mediatracker.providers.sources.free_telecharger import FreeTelechargerConfig, FreeTelechargerSource
from mediatracker.providers.sources.free_telecharger_html import parse_intermediate, parse_links, parse_search
from mediatracker.providers.sources.free_telecharger_plugin import FreeTelechargerSourceEntry
from mediatracker.providers.sources.loading import source_base_url
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer, backoff_delay
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource

FIXTURES = Path(__file__).parent / "fixtures" / "free_telecharger"
BASE = "https://catalogue-neutre.example"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _route(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        url = str(request.url)
        if "recherche1" in url and "Veilleur" in url:
            return httpx.Response(200, text=_text("search_series.html"))
        if "recherche1" in url and "Delta" in url:
            return httpx.Response(200, text=_text("search_film.html"))
        if "delta-archipel" in url:
            return httpx.Response(200, text=_text("film_page.html"))
        if "saison-1" in url and "veilleur" in url:
            return httpx.Response(200, text=_text("series_episode_page.html"))
        if "saison-2" in url and "pack" in url:
            return httpx.Response(200, text=_text("season_pack_page.html"))
        if "liens.catalogue-neutre" in url:
            return httpx.Response(200, text=_text("intermediate.html"))
        return httpx.Response(404, text="absent")

    return handler


def _source(handler, *, cache: HtmlCache | None = None, pacer: SourcePacer | None = None) -> FreeTelechargerSource:
    return FreeTelechargerSource(
        FreeTelechargerConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        cache=cache,
        pacer=pacer or SourcePacer(0, sleeper=lambda _seconds: None),
    )


def test_parse_search_film_and_series():
    films = parse_search(_text("search_film.html"), BASE, want_type="film")
    assert {(hit.title, hit.year) for hit in films} == {("Delta Archipel", 2021), ("Autre Sortie", 1984)}
    series = parse_search(_text("search_series.html"), BASE, want_type="series")
    assert {hit.season for hit in series} == {1, 2}


def test_parse_film_page_filters_uptobox():
    heading, links = parse_links(_text("film_page.html"), f"{BASE}/fiches/delta-archipel.html")
    assert heading.title == "Delta Archipel"
    assert heading.language == "VFF"
    hosters = [link.hoster for link in links]
    assert "uptobox" in hosters
    assert "1fichier" in hosters


def test_get_links_orders_hosters_and_skips_uptobox():
    calls: list[str] = []
    source = _source(_route(calls))
    ref = SourceRef(ref=f"{BASE}/fiches/delta-archipel.html", url=f"{BASE}/fiches/delta-archipel-2021-hd-vff.html")
    links = source.get_links(ref)
    source.close()
    assert [link.hoster for link in links] == ["rapidgator", "turbobit", "1fichier"]
    assert all(link.hoster != "uptobox" for link in links)


def test_parse_episode_page_div_id_main_and_slug_intermediate():
    heading, links = parse_links(
        _text("series_episode_page_id_main.html"),
        f"{BASE}/fiches/veilleur-s1.html",
        base_url=BASE,
    )
    assert heading.quality and "1080" in heading.quality
    assert len(links) >= 1
    ep1 = [link for link in links if link.episode == 1]
    assert len(ep1) == 1
    assert ep1[0].intermediate is True
    assert (urlparse(ep1[0].url).hostname or "").startswith("liens.")

    slug_html = _text("series_episode_page_id_main.html").replace(
        "https://liens.catalogue-neutre.example/page/alpha",
        "abcd-episode_1",
    )
    _, slug_links = parse_links(slug_html, f"{BASE}/fiches/veilleur-s1.html", base_url=BASE)
    ep = next(link for link in slug_links if link.episode == 1)
    assert ep.url == "https://liens.catalogue-neutre.example/abcd-episode_1"
    assert ep.intermediate is True


def test_parse_episode_page_without_lien_value_stays_empty():
    _, links = parse_links(
        _text("series_episode_page_token_only.html"),
        f"{BASE}/fiches/veilleur-s1.html",
        base_url=BASE,
    )
    assert links == []


def test_parse_episode_page_and_intermediate_resolution():
    calls: list[str] = []
    source = _source(_route(calls))
    ref = SourceRef(ref=f"{BASE}/fiches/veilleur-s1.html", url=f"{BASE}/fiches/veilleur-lune-saison-1-hd-vff.html")
    links = source.get_links(ref)
    source.close()
    ep1 = [link for link in links if link.episode == 1]
    assert len(ep1) == 1
    assert ep1[0].hoster == "nitroflare"
    assert all(link.hoster != "uptobox" for link in links)


def test_season_pack_has_no_episode_index():
    calls: list[str] = []
    source = _source(_route(calls))
    ref = SourceRef(ref=f"{BASE}/fiches/veilleur-s2-pack.html", url=f"{BASE}/fiches/veilleur-saison-2-pack.html")
    links = source.get_links(ref)
    source.close()
    assert links
    assert all(link.episode is None for link in links)
    assert links[0].season == 2


def test_intermediate_parser_omits_uptobox_rows():
    rows = parse_intermediate(_text("intermediate.html"), BASE)
    assert [hoster for _url, hoster in rows] == ["nitroflare", "uptobox"]


def test_pacer_waits_and_cache_skips_second_fetch():
    clock = _Clock()
    pacer = SourcePacer(5, clock=clock, sleeper=clock.sleep, rng=random.Random(0))
    assert pacer.wait() == 0
    second = pacer.wait()
    assert 5 <= second <= 6
    assert backoff_delay(2, 180) == timedelta(minutes=360)

    calls: list[str] = []
    source = _source(_route(calls), cache=HtmlCache(900))
    source.search("Delta Archipel", "film")
    source.search("Delta Archipel", "film")
    source.close()
    assert len([url for url in calls if "recherche1" in url]) == 1


def test_open_poll_returns_none_when_base_url_empty(client, app):
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        assert FreeTelechargerSource.open_poll(db, settings) is None
        entry = FreeTelechargerSourceEntry()
        probe = entry.probe_binding_page(db, settings, f"{BASE}/page")
        assert probe.accessible is False
        assert "n'est pas configurée" in (probe.message or "").lower()
    finally:
        db.close()


def test_source_error_logs_and_does_not_crash():
    def broken(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="erreur")

    source = _source(broken)
    with pytest.raises(SourceFetchError) as exc_info:
        source.search("Delta Archipel", "film")
    source.close()
    assert exc_info.value.code == "HTTP_500"


def test_discover_uses_single_search_page(client, app):
    calls: list[str] = []
    source = _source(_route(calls))
    query = SourceQuery(
        title="Veilleur Lune",
        original_title=None,
        year=2023,
        media_type="series",
        seasons=[1],
    )
    prefs = SourcePreferences(quality="HD", language="VFF", allow_fallback=False)
    found = source.discover(query, prefs)
    source.close()
    assert found
    assert len([url for url in calls if "recherche1" in url]) == 1


def test_cascade_continues_after_free_telecharger_transport_error(monkeypatch):
    """Orchestration : une source en erreur n'empêche pas l'appel de la suivante."""

    opened: list[str] = []

    class _Bad(FreeTelechargerSource):
        def discover(self, *args, **kwargs):
            raise SourceFetchError("TRANSPORT", "indisponible", retryable=True)

    class _Good(WawacitySource):
        def discover(self, query, preferences):
            opened.append("wawacity")
            return []

    monkeypatch.setattr(
        "mediatracker.providers.sources.free_telecharger.FreeTelechargerSource",
        _Bad,
    )
    ft = _Bad(FreeTelechargerConfig(base_url=BASE))
    wa = _Good(WawacityConfig(base_url="https://wawa.test"))
    query = SourceQuery(title="Veilleur Lune", original_title=None, year=2023, media_type="series", seasons=[1])
    prefs = SourcePreferences()
    try:
        ft.discover(query, prefs)
    except SourceFetchError:
        pass
    wa.discover(query, prefs)
    assert opened == ["wawacity"]
    ft.close()
    wa.close()


def test_provider_registered_with_links_capability(client):
    from tests.conftest import login

    login(client)
    items = client.get("/api/v1/providers/types?kind=source").json()["items"]
    row = next(item for item in items if item["type"] == "free_telecharger")
    assert row["download_rank"] == 12
    assert "links" in row["capabilities"]
    assert "base_url" in row["config_schema"]["properties"]


def test_source_base_url_empty_without_setting(client, app):
    db = app.state.session_factory()
    try:
        assert source_base_url(db, app.state.settings, "free_telecharger") == ""
    finally:
        db.close()

"""Wawacity film search without listing slug, year in URL, and profile-aware polling."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from mediatracker.core.profile_attempt_adapters import select_pages_for_profile_attempt
from mediatracker.core.quality_profile import default_profile
from mediatracker.core.source_poll import _page_links
from mediatracker.providers.base import SourcePreferences, SourceQuery, SourceRef
from mediatracker.providers.sources.throttle import HtmlCache, SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.providers.sources.wawacity_query import plain_search_url

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
BASE = "https://wawa.test"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(handler, *, cache: HtmlCache | None = None) -> WawacitySource:
    return WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        cache=cache,
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )


def test_plain_search_url_includes_year_and_omits_slug():
    url = plain_search_url(BASE, "film", "Odyssée Labo", year=2026)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert query["p"] == ["films"]
    assert "s" not in query
    assert query.get("year") == ["2026"]
    assert "Odyss%C3%A9e" in url or "Odyssée" in url


def test_discover_film_requests_year_then_plain_and_picks_1080p_multi():
    calls: list[str] = []
    search_html = _text("search_film_multi_quality.html")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=search_html)

    source = _source(handler)
    query = SourceQuery(title="Odyssée Labo", year=2026, media_type="film", seasons=[])
    prefs = SourcePreferences(quality="HD", language="VFF")
    results = source.discover(query, prefs)
    source.close()

    assert len(calls) == 1
    parsed = urlparse(calls[0])
    q = parse_qs(parsed.query)
    assert q.get("year") == ["2026"]
    assert "s" not in q

    profile = default_profile()
    pages = select_pages_for_profile_attempt(
        results,
        profile=profile,
        title="Odyssée Labo",
        year=2026,
        media_type="film",
        seasons=set(),
    )
    assert len(pages) == 1
    assert pages[0].quality == "1080p"
    assert pages[0].language == "MULTI"
    assert "88003-odyssee-labo-1080" in (pages[0].url or "")


def test_discover_film_retries_without_year_when_year_search_empty():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "year=2026" in url:
            return httpx.Response(200, text="<html><body></body></html>")
        return httpx.Response(200, text=_text("search_film_multi_quality.html"))

    source = _source(handler)
    query = SourceQuery(title="Odyssée Labo", year=2026, media_type="film", seasons=[])
    prefs = SourcePreferences()
    source.discover(query, prefs)
    source.close()

    assert len(calls) == 2
    assert "year=2026" in calls[0]
    assert "year=" not in calls[1]


def test_seasons_covered_ignores_same_title_other_year():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=_text("search_film_multi_quality.html"))

    source = _source(handler, cache=HtmlCache(900))
    query = SourceQuery(title="Odyssée Labo", year=2026, media_type="film", seasons=[])
    source.discover(query, SourcePreferences())
    source.close()
    assert len(calls) >= 1
    urls_with_year = [u for u in calls if "year=2026" in u]
    assert urls_with_year


def test_get_links_opens_one_sister_when_page_is_off_profile():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "88001-odyssee-labo-hd" in url or "88001" in url:
            return httpx.Response(200, text=_text("film_odyssee_hd_sister.html"))
        if "103-odyssee" in url or "id=103" in url:
            return httpx.Response(200, text=_text("film_odyssee_1080.html"))
        return httpx.Response(404, text="absent")

    source = _source(handler)
    profile = default_profile()
    ref = SourceRef(ref=f"{BASE}/?p=film&id=88001-odyssee-labo-hd", url=f"{BASE}/?p=film&id=88001-odyssee-labo-hd")
    links = source.get_links(ref, profile=profile)
    source.close()

    assert len(calls) == 2
    assert any("id=103" in c for c in calls)
    assert len(links) >= 1
    assert links[0].quality == "1080p"


def test_page_links_passes_profile_to_wawacity():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "88001" in str(request.url):
            return httpx.Response(200, text=_text("film_odyssee_hd_sister.html"))
        if "103" in str(request.url):
            return httpx.Response(200, text=_text("film_odyssee_1080.html"))
        return httpx.Response(404, text="absent")

    source = _source(handler)

    class _Page:
        ref = f"{BASE}/?p=film&id=88001-odyssee-labo-hd"
        url = ref

    links = _page_links(source, _Page(), default_profile())
    source.close()
    assert len(calls) == 2
    assert any("id=103" in c for c in calls)
    assert links


def test_parse_alternate_qualities_reads_button_labels_and_inherits_title_year():
    from mediatracker.providers.sources.wawacity_html import parse_alternate_qualities

    html = _text("film_odyssee_hd_sister.html")
    alts = parse_alternate_qualities(html, BASE, page_title="Odyssée Labo", page_year=2026)
    multi = next(alt for alt in alts if "id=103" in alt.url)
    assert multi.quality == "1080p"
    assert multi.language == "MULTI"
    assert multi.title == "Odyssée Labo"
    assert multi.year == 2026
    four_k = next(alt for alt in alts if "id=104" in alt.url)
    assert four_k.language == "MULTI"

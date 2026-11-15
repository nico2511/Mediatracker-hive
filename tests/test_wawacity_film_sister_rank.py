"""Film get_links et découverte : préférence MULTi VFF/VF2 face à VFQ."""

from pathlib import Path

import httpx

from mediatracker.core.profile_attempt_adapters import select_pages_for_profile_attempt
from mediatracker.core.quality_profile import default_profile
from mediatracker.providers.base import SourcePreferences, SourceQuery, SourceRef
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.wawacity import WawacityConfig, WawacitySource
from mediatracker.providers.sources.wawacity_html import parse_search

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity_sister_rank"
BASE = "https://fiches.example"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _source(pages: dict[str, str]) -> WawacitySource:
    def handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        for needle, html in pages.items():
            if needle in key:
                return httpx.Response(200, text=html)
        return httpx.Response(404, text="absent")

    return WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )


def _ref(suffix: str) -> SourceRef:
    url = f"{BASE}/?p=film&id={suffix}"
    return SourceRef(ref=url, url=url)


def test_get_links_switches_from_multi_vfq_to_vf2_sister():
    calls: list[str] = []
    pages = {
        "vfq-fictif-linked": _text("film_multi_vfq_linked_vf2_only.html"),
        "vf2-fictif-1080": _text("film_multi_vf2_page.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        for needle, html in pages.items():
            if needle in url:
                return httpx.Response(200, text=html)
        return httpx.Response(404, text="absent")

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    links = source.get_links(_ref("vfq-fictif-linked"), profile=default_profile())
    source.close()

    assert len(calls) == 2
    assert any("vf2-fictif-1080" in call for call in calls)
    assert links and "fictif-vf2-rg" in links[0].url


def test_get_links_switches_from_multi_vfq_to_vff_sister():
    calls: list[str] = []
    pages = {
        "vfq-fictif-linked-vff": _text("film_multi_vfq_linked_vff_only.html"),
        "vff-fictif-1080": _text("film_multi_vff_page.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        for needle, html in pages.items():
            if needle in url:
                return httpx.Response(200, text=html)
        return httpx.Response(404, text="absent")

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    links = source.get_links(_ref("vfq-fictif-linked-vff"), profile=default_profile())
    source.close()

    assert len(calls) == 2
    assert any("vff-fictif-1080" in call for call in calls)
    assert links and "fictif-vff-rg" in links[0].url


def test_get_links_keeps_multi_vff_when_sister_is_vfq():
    calls: list[str] = []
    pages = {
        "vff-fictif-linked": _text("film_multi_vff_linked_vfq_sister.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=pages["vff-fictif-linked"])

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    links = source.get_links(_ref("vff-fictif-linked"), profile=default_profile())
    source.close()

    assert len(calls) == 1
    assert links and "fictif-vff-linked-rg" in links[0].url


def test_get_links_keeps_linked_vfq_when_no_better_sister():
    calls: list[str] = []
    html = _text("film_multi_vfq_alone.html")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=html)

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    links = source.get_links(_ref("vfq-fictif-alone"), profile=default_profile())
    source.close()

    assert len(calls) == 1
    assert links and "fictif-vfq-only-rg" in links[0].url


def test_parse_search_keeps_multi_variant_in_profile_hint():
    html = _text("search_film_multi_vf_variants.html")
    hits = parse_search(html, BASE)
    by_id = {hit.url.split("id=")[-1]: hit for hit in hits if "id=" in hit.url}
    assert "VFQ" in by_id["vfq-fictif-1080"].profile_hint.upper()
    assert "VF2" in by_id["vf2-fictif-1080"].profile_hint.upper()


def test_discover_film_prefers_multi_vf2_over_vfq_in_search():
    calls: list[str] = []
    search_html = _text("search_film_multi_vf_variants.html")

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, text=search_html)

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    query = SourceQuery(title="Oeuvre Fictive", year=2099, media_type="film", seasons=[])
    results = source.discover(query, SourcePreferences())
    source.close()

    assert len(calls) == 1
    profile = default_profile()
    pages = select_pages_for_profile_attempt(
        results,
        profile=profile,
        title="Oeuvre Fictive",
        year=2099,
        media_type="film",
        seasons=set(),
    )
    assert len(pages) == 1
    assert "vf2-fictif-1080" in (pages[0].url or "")


def test_discover_pick_vfq_then_get_links_upgrades_to_vf2_sister():
    """Recherche sans VF2 : fiche VFQ retenue à la découverte, get_links bascule sur la sœur."""
    calls: list[str] = []
    search_vfq_only = """<!DOCTYPE html>
<html lang="fr"><body>
  <div class="wa-sub-block wa-post-detail-item">
    <div class="wa-sub-block-title">
      <a href="?p=film&amp;id=vfq-fictif-1080">Oeuvre Fictive<i> [WEB-DL 1080p] - MULTI (VFQ)</i></a>
    </div>
    <div class="row wa-post-detail-list">
      <ul><li class="col-md-6"><span>Année:</span> <b>2099</b></li></ul>
    </div>
  </div>
</body></html>"""
    page_fixtures = {
        "vfq-fictif-1080": _text("film_multi_vfq_linked_vf2_only.html"),
        "vf2-fictif-1080": _text("film_multi_vf2_page.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        for needle, html in page_fixtures.items():
            if needle in url and "?p=film" in url:
                return httpx.Response(200, text=html)
        return httpx.Response(200, text=search_vfq_only)

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    query = SourceQuery(title="Oeuvre Fictive", year=2099, media_type="film", seasons=[])
    results = source.discover(query, SourcePreferences())
    profile = default_profile()
    chosen = select_pages_for_profile_attempt(
        results,
        profile=profile,
        title="Oeuvre Fictive",
        year=2099,
        media_type="film",
        seasons=set(),
    )
    assert len(chosen) == 1
    assert "vfq-fictif-1080" in (chosen[0].url or "")
    links = source.get_links(SourceRef(ref=chosen[0].ref, url=chosen[0].url), profile=profile)
    source.close()

    page_calls = [c for c in calls if "p=film" in c and "id=" in c]
    assert len(page_calls) == 2
    assert any("vf2-fictif-1080" in c for c in page_calls)
    assert links and "fictif-vf2-rg" in links[0].url


def test_get_links_still_rescues_rejected_page_via_sister():
    calls: list[str] = []
    pages = {
        "hd-fictif-rejected": _text("film_hd_rejected_sister_1080.html"),
        "multi-vfq-rescue-1080": _text("film_multi_vfq_rescue_page.html"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        for needle, html in pages.items():
            if needle in url:
                return httpx.Response(200, text=html)
        return httpx.Response(404, text="absent")

    source = WawacitySource(
        WawacityConfig(base_url=BASE, user_agent="TestAgent/1.0"),
        transport=httpx.MockTransport(handler),
        pacer=SourcePacer(0, sleeper=lambda _seconds: None),
    )
    links = source.get_links(_ref("hd-fictif-rejected"), profile=default_profile())
    source.close()

    assert len(calls) == 2
    assert any("multi-vfq-rescue-1080" in call for call in calls)
    assert links and links[0].quality == "1080p"
    assert "fictif-rescue-rg" in links[0].url

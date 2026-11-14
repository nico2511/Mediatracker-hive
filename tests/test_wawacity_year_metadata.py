"""Wawacity year metadata in result blocks and detail pages (realistic HTML shape)."""

from pathlib import Path

from mediatracker.core.source_select import identity_match
from mediatracker.providers.sources.wawacity_html import (
    canon_language,
    parse_heading,
    parse_links,
    parse_listing,
    parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wawacity"
BASE = "https://wawa.test"


def test_canon_language_prefers_multi_over_truefrench():
    assert canon_language("[WEB-DL 1080p] - MULTI (TRUEFRENCH)") == "MULTI"
    assert canon_language("[WEB-DL 1080p] - MULTI (FRENCH)") == "MULTI"
    assert canon_language("[WEBRIP] - TRUEFRENCH") == "VFF"
    assert canon_language(" - FRENCH") == "VF"


def test_parse_search_reads_year_from_same_block():
    hits = parse_search((FIXTURES / "search_year_from_block.html").read_text(encoding="utf-8"), BASE)
    by_slug = {hit.url.split("id=")[-1]: hit for hit in hits}
    circuit = by_slug["90001-circuit-fabuleux"]
    assert circuit.title == "Circuit Fabuleux"
    assert circuit.year == 2026
    assert circuit.language == "MULTI"
    assert by_slug["90003-atlas-d-automne"].year == 2017


def test_parse_search_does_not_leak_year_across_blocks():
    hits = parse_search((FIXTURES / "search_year_from_block.html").read_text(encoding="utf-8"), BASE)
    sans = next(hit for hit in hits if "90002-riviere-sans-annee" in hit.url)
    assert sans.year is None


def test_parse_listing_reads_block_year():
    hits = parse_listing((FIXTURES / "search_year_from_block.html").read_text(encoding="utf-8"), BASE, "film")
    circuit = next(hit for hit in hits if "90001-circuit-fabuleux" in hit.url)
    assert circuit.year == 2026


def test_parse_links_reads_year_from_detail_page_not_sidebar():
    heading, _links = parse_links(
        (FIXTURES / "film_page_year_from_detail.html").read_text(encoding="utf-8"),
        f"{BASE}/?p=film&id=90001-circuit-fabuleux",
    )
    assert heading.title == "Circuit Fabuleux"
    assert heading.year == 2026
    assert heading.language == "VFF"


def test_identity_match_accepts_search_hit_with_block_year():
    hits = parse_search((FIXTURES / "search_year_from_block.html").read_text(encoding="utf-8"), BASE)
    circuit = next(hit for hit in hits if "90001-circuit-fabuleux" in hit.url)
    assert identity_match(
        circuit.title,
        circuit.year,
        title="Circuit Fabuleux",
        original_title=None,
        year=2026,
        media_type="film",
    )


def test_heading_without_year_still_parses_title():
    heading = parse_heading("Circuit Fabuleux [WEB-DL 1080p] - MULTI (TRUEFRENCH)")
    assert heading.title == "Circuit Fabuleux"
    assert heading.year is None
    assert heading.language == "MULTI"

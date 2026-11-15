"""Jeton CA (VFQ canadien) adjacent à MULTI ou marqueur de langue — noms fictifs."""

import pytest

from mediatracker.core.quality_profile import (
    ReleaseCandidate,
    default_profile,
    detect_language,
    multi_french_variant,
    normalize_language,
    sort_key,
)
from mediatracker.core.source_select import canonical_language
from mediatracker.library.parse import parse_release
from mediatracker.providers.sources.wawacity_html import canon_language, parse_heading, release_name_from_row

_CA_MULTI_DOT = "Oeuvre.Factice.2026.MULTi.CA.1080p.WEB.H264-GRP.mkv"
_CA_MULTI_SPACE = "Oeuvre.Factice.2026.MULTI CA 1080p.x264-GRP.mkv"
_CA_FRENCH = "Oeuvre.Factice.2026.FRENCH.CA.1080p.x264-GRP.mkv"
_CA_SOLO_TITLE = "Oeuvre.CA.Factice.2026.1080p.x264-GRP.mkv"
_CA_GROUP_ONLY = "Oeuvre.Factice.2026.1080p.x264-CA.mkv"


def test_multi_ca_stays_multi_variant_vfq():
    assert detect_language(_CA_MULTI_DOT) == "MULTI"
    assert multi_french_variant(_CA_MULTI_DOT) == "VFQ"
    assert parse_release(_CA_MULTI_DOT).language == "MULTI"


def test_multi_ca_space_separator():
    assert detect_language(_CA_MULTI_SPACE) == "MULTI"
    assert multi_french_variant(_CA_MULTI_SPACE) == "VFQ"


def test_french_ca_detected_as_vfq_not_vf():
    assert detect_language(_CA_FRENCH) == "VFQ"
    assert parse_release(_CA_FRENCH).language == "VFQ"


def test_isolated_ca_in_title_not_vfq():
    assert detect_language(_CA_SOLO_TITLE) is None
    assert multi_french_variant(_CA_SOLO_TITLE) is None
    assert normalize_language("CA") is None
    assert canonical_language("CA") != "VFQ"


def test_library_parse_keeps_title_starting_with_ca():
    assert parse_release("Ca.Fictif.2026.MULTi.1080p.mkv").title == "Ca Fictif"
    assert parse_release("Ca.2026.MULTi.1080p.mkv").title == "Ca"


def test_library_parse_title_ending_in_ca_not_truncated():
    assert parse_release("Amerique.Fictica.2026.MULTi.1080p.mkv").title == "Amerique Fictica"


def test_wawacity_release_row_not_matched_on_fictica_suffix():
    row = "Lien 1: Amerique.Fictica.2026.MULTi.1080p.x264.mkv | Uploady | 4 Go"
    assert release_name_from_row(row) == "Amerique.Fictica.2026.MULTi.1080p.x264.mkv"


def test_multi_ca_title_strips_contextual_ca_only():
    assert parse_release(_CA_MULTI_DOT).title == "Oeuvre Factice"
    assert multi_french_variant(_CA_MULTI_DOT) == "VFQ"


def test_isolated_ca_release_group_not_vfq():
    assert detect_language(_CA_GROUP_ONLY) is None
    assert multi_french_variant(_CA_GROUP_ONLY) is None


def test_wawacity_heading_multi_paren_ca():
    raw = "Films » Oeuvre Fictive [WEB-DL 1080p] - MULTI (CA) (2099)"
    heading = parse_heading(raw)
    assert heading.language == "MULTI"
    assert heading.title == "Oeuvre Fictive"
    assert multi_french_variant(raw) == "VFQ"


def test_wawacity_canon_language_multi_ca():
    assert canon_language("WEB-DL 1080p - MULTI (CA)") == "MULTI"
    assert multi_french_variant("WEB-DL 1080p - MULTI (CA)") == "VFQ"


def _multi_candidate(text: str, *, seeders: int = 0, size: int = 0) -> ReleaseCandidate:
    return ReleaseCandidate(
        language="MULTI",
        resolution="1080p",
        seeders=seeders,
        size=size,
        text=text,
    )


def test_multi_ca_ranked_after_vff_and_vf2():
    profile = default_profile()
    vff = _multi_candidate("Oeuvre.Factice.2026.MULTi.VFF.1080p.x264.mkv", seeders=1)
    vf2 = _multi_candidate("Oeuvre.Factice.2026.MULTi.VF2.1080p.x264.mkv", seeders=1)
    ca = _multi_candidate(_CA_MULTI_DOT, seeders=999, size=9_000_000_000)
    vfq = _multi_candidate("Oeuvre.Factice.2026.MULTi.VFQ.1080p.x264.mkv", seeders=999, size=9_000_000_000)
    assert sort_key(vff, profile) < sort_key(ca, profile)
    assert sort_key(vf2, profile) < sort_key(ca, profile)
    assert sort_key(ca, profile)[:2] == sort_key(vfq, profile)[:2]


@pytest.mark.parametrize(
    "blob,expected",
    (
        ("Oeuvre.Factice.1080p.VFF.x264.mkv", "VFF"),
        ("Oeuvre.Factice.1080p.VFQ.x264.mkv", "VFQ"),
        ("Oeuvre.Factice.2026.MULTi.VF2.1080p.x264.mkv", "MULTI"),
        ("Oeuvre.Factice.2026.VF2.1080p.x264.mkv", "VFF"),
    ),
)
def test_vf2_vff_vfq_non_regression(blob, expected):
    assert detect_language(blob) == expected
    assert parse_release(blob).language == (expected if expected != "MULTI" else "MULTI")

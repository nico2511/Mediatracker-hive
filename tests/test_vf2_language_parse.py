"""Jeton VF2 (VFF+VFQ) et canaux audio dans les noms de release fictifs."""

import pytest

from mediatracker.core.quality_profile import (
    ReleaseCandidate,
    default_profile,
    detect_language,
    filter_candidates,
    normalize_language,
    sort_key,
)
from mediatracker.core.source_select import canonical_language
from mediatracker.library.parse import parse_release

_VF2_MULTI = "Titre.Factice.2026.MULTi.VF2.1080p.BluRay.EAC3.5.1.x264-GRP.mkv"
_VF2_SOLO = "Titre.Factice.2026.VF2.1080p.BluRay.EAC3.5.1.x264-GRP.mkv"


def test_vf2_solo_detected_as_vff():
    assert detect_language(_VF2_SOLO) == "VFF"
    assert normalize_language("VF2") == "VFF"
    assert canonical_language("VF2") == "VFF"


def test_multi_vf2_stays_multi():
    assert detect_language(_VF2_MULTI) == "MULTI"
    parsed = parse_release(_VF2_MULTI)
    assert parsed.language == "MULTI"


def test_vf2_release_title_stripped():
    parsed = parse_release(_VF2_SOLO)
    assert parsed.language == "VFF"
    assert parsed.title == "Titre Factice"
    assert "VF2" not in parsed.title
    assert "5 1" not in parsed.title


def test_vf2_multi_title_stripped():
    parsed = parse_release(_VF2_MULTI)
    assert parsed.title == "Titre Factice"
    assert "VF2" not in parsed.title
    assert "5 1" not in parsed.title


@pytest.mark.parametrize(
    "blob,expected",
    (
        ("Oeuvre.Factice.1080p.VFF.x264.mkv", "VFF"),
        ("Oeuvre.Factice.1080p.VFQ.x264.mkv", "VFQ"),
        ("Oeuvre.Factice.1080p.VF.x264.mkv", "VF"),
        ("Oeuvre.Factice.1080p.MULTi.x264.mkv", "MULTI"),
        ("Oeuvre.Factice.1080p.VOSTFR.x264.mkv", "VOSTFR"),
    ),
)
def test_language_tokens_unchanged(blob, expected):
    assert detect_language(blob) == expected
    assert parse_release(blob).language == expected


def test_audio_channel_noise_removed_from_title():
    for channels in ("5.1", "7.1", "2.0"):
        stem = f"Oeuvre.Factice.2026.1080p.EAC3.{channels}.x264-GRP.mkv"
        assert channels.replace(".", " ") not in parse_release(stem).title
        assert parse_release(stem).title == "Oeuvre Factice"


def _multi_candidate(text: str, *, seeders: int = 0, size: int = 0) -> ReleaseCandidate:
    return ReleaseCandidate(
        language="MULTI",
        resolution="1080p",
        seeders=seeders,
        size=size,
        text=text,
    )


def test_multi_vf2_and_vff_rank_before_vfq_despite_seeders():
    profile = default_profile()
    vfq = _multi_candidate("Oeuvre.Factice.2026.MULTi.VFQ.1080p.x264.mkv", seeders=999, size=9_000_000_000)
    vf2 = _multi_candidate("Oeuvre.Factice.2026.MULTi.VF2.1080p.x264.mkv", seeders=1, size=1_000_000)
    vff = _multi_candidate("Oeuvre.Factice.2026.MULTi.VFF.1080p.x264.mkv", seeders=1, size=1_000_000)
    assert sort_key(vf2, profile) < sort_key(vfq, profile)
    assert sort_key(vff, profile) < sort_key(vfq, profile)


def test_multi_vfq_alone_still_accepted():
    profile = default_profile()
    vfq = _multi_candidate("Oeuvre.Factice.2026.MULTi.VFQ.1080p.x264.mkv")
    kept, dropped = filter_candidates([vfq], profile, enforce_size=False)
    assert dropped == 0
    assert kept == [0]


def test_non_multi_sort_unchanged():
    profile = default_profile()
    vff = ReleaseCandidate(language="VFF", resolution="1080p", text="", seeders=1)
    vf = ReleaseCandidate(language="VF", resolution="1080p", text="", seeders=99)
    assert sort_key(vff, profile) < sort_key(vf, profile)

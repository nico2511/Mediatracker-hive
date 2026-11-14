"""Wawacity FRENCH/HDLight, messages multi-sources et sondage rapide."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from mediatracker.core.pipeline.download_pass_errors import (
    begin_download_pass,
    compose_download_pass_failure,
    record_download_pass_verdict,
    wawacity_had_no_stored_links,
)
from mediatracker.core.pipeline.jobs import _fast_source_poll, _throttle_poll
from mediatracker.core.quality_profile import (
    HDLIGHT1080_RESOLUTION,
    RESOLUTION_RANK,
    ReleaseCandidate,
    acceptance_rank,
    builtin_profiles,
    default_profile,
    normalize_language,
    normalize_resolution,
    passes,
)
from mediatracker.providers.sources.wawacity_html import parse_heading

NOW = datetime(2026, 11, 12, 20, tzinfo=timezone.utc)


def test_default_profile_accepts_1080p_rejects_hdlight_below_floor():
    heading = parse_heading("[HDLIGHT 1080p] - FRENCH")
    assert heading.language == "VF"
    assert heading.quality == "HDLight1080"
    profile = default_profile()
    hdlight = ReleaseCandidate(
        language=normalize_language(heading.language),
        resolution=normalize_resolution(heading.quality),
        text="",
    )
    assert not passes(hdlight, profile, enforce_size=False)
    full_hd = ReleaseCandidate(language="VF", resolution="1080p", text="")
    assert passes(full_hd, profile, enforce_size=False)


def test_title_with_vo_substring_is_not_classified_vo():
    heading = parse_heading("Volcan Démo [HDLIGHT 1080p] - FRENCH")
    assert heading.language == "VF"
    assert heading.title == "Volcan Démo"


def test_heading_strips_multi_french_suffix_from_title():
    heading = parse_heading("Échantillon Démo [HD] - MULTI (FRENCH)")
    assert heading.language == "MULTI"
    assert heading.title == "Échantillon Démo"
    assert "(FRENCH)" not in heading.title


def test_compose_failure_message_lists_wawacity_then_comet():
    row = SimpleNamespace()
    begin_download_pass(row)
    record_download_pass_verdict(
        row,
        "wawacity",
        code="NO_LINK",
        message="aucun lien enregistré",
        issue="empty",
    )
    record_download_pass_verdict(
        row,
        "comet",
        code="PROFILE",
        message="aucun flux au profil demandé.",
        issue="refused",
    )
    code, message = compose_download_pass_failure(row, ["wawacity", "comet"])
    assert code == "NO_LINK"
    assert "Wawacity : aucun lien enregistré" in message
    assert "Comet : aucun flux au profil demandé." in message
    assert " · " in message
    assert wawacity_had_no_stored_links(row)


def test_fast_poll_instead_of_180_min_backoff(settings, monkeypatch):
    from mediatracker.db.models import Media

    media = Media(
        title="Sujet Démo",
        type="film",
        year=2020,
        followed=True,
        next_source_poll_at=NOW + timedelta(hours=3),
    )
    db = MagicMock()
    db.scalar.return_value = settings.source_error_backoff_min
    _throttle_poll(db, media, settings, NOW)
    assert media.next_source_poll_at >= NOW + timedelta(minutes=29)
    media.next_source_poll_at = NOW + timedelta(hours=3)
    woke = MagicMock()
    monkeypatch.setattr("mediatracker.core.worker_wake.wake_worker", woke)
    _fast_source_poll(db, media, settings, NOW)
    assert media.next_source_poll_at == NOW
    woke.assert_called_once()


def test_hdlight_1080_ranks_above_hd_720():
    profile = {seed.name: seed for seed in builtin_profiles()}["Meilleure dispo"]
    hd = acceptance_rank(ReleaseCandidate(language="VF", resolution="HD", text=""), profile)
    light = acceptance_rank(
        ReleaseCandidate(language="VF", resolution=HDLIGHT1080_RESOLUTION, text=""),
        profile,
    )
    assert hd is not None and light is not None
    assert light < hd


def test_builtin_hdlight_profile_present():
    by_name = {seed.name: seed for seed in builtin_profiles()}
    profile = by_name["HDLight VF/Multi"]
    assert profile.languages == ("VFF", "VFQ", "VF", "MULTI")
    assert profile.min_resolution == "HDLight1080"
    assert profile.max_resolution == "HDLight1080"


def test_hdlight1080_in_resolution_scale():
    assert RESOLUTION_RANK["HDLight"] < RESOLUTION_RANK["HD"]
    assert RESOLUTION_RANK["HD"] < RESOLUTION_RANK["HDLight1080"]
    assert RESOLUTION_RANK["HDLight1080"] < RESOLUTION_RANK["1080p"]

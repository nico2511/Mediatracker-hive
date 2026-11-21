"""Lot B : adaptateurs distants pour une seule tentative de profil explicite."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mediatracker.core.profile_attempt import iter_profile_source_attempts, walk_profile_source_attempts
from mediatracker.core.profile_attempt_adapters import (
    NON_WIRED_PROFILE_ATTEMPT_CALL_SITES,
    attempt_selection_criteria,
    choose_link_for_profile_attempt,
    comet_context_for_attempt,
    legacy_comet_context,
    rank_link_for_profile_attempt,
    rank_pages_for_profile_attempt,
    reingest_select_pages,
    select_comet_streams_for_profile_attempt,
    select_pages_for_profile_attempt,
)
from mediatracker.core.quality_profile import Profile, builtin_profiles, default_profile
from mediatracker.core.source_select import select_variants
from mediatracker.db.models import Link, Media
from mediatracker.providers.base import EpisodeQuery, SourceResult

FIXTURES = Path(__file__).parent / "fixtures" / "comet"
DEMO_SERIES = [
    SourceResult(
        title="Parcours Démo",
        year=2022,
        media_type="series",
        ref="vff-hd",
        url="http://vff-hd",
        season=1,
        quality="HD",
        language="VFF",
    ),
    SourceResult(
        title="Parcours Démo",
        year=2022,
        media_type="series",
        ref="vost-1080",
        url="http://vost",
        season=1,
        quality="1080p",
        language="VOSTFR",
    ),
]


def _vostfr_profile() -> Profile:
    return next(profile for profile in builtin_profiles() if profile.name == "VOSTFR 1080p")


def _principal_profile() -> Profile:
    """Profil principal strict (VFF) — le repli VOSTFR n'est pas accepté implicitement."""
    return Profile(
        name="Principal VFF",
        languages=("VFF",),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def test_explicit_profile_prevails_over_legacy_csv_quality_language():
    """Avec profil explicite, les listes CSV legacy ne doivent pas élargir la sélection."""
    principal = _principal_profile()
    trap_csv = ("SD", "4K")
    trap_lang = ("VO", "EN", "VOSTFR")
    legacy_without_profile = select_variants(
        list(DEMO_SERIES),
        title="Parcours Démo",
        year=2022,
        media_type="series",
        seasons={1},
        quality="1080p",
        language="VOSTFR",
        allow_fallback=True,
        quality_fallback=trap_csv,
        language_fallback=trap_lang,
        profile=None,
    )
    assert [page.ref for page in legacy_without_profile] == ["vost-1080"]

    misleading_args = select_variants(
        list(DEMO_SERIES),
        title="Parcours Démo",
        year=2022,
        media_type="series",
        seasons={1},
        quality="VO",
        language="EN",
        allow_fallback=True,
        quality_fallback=trap_csv,
        language_fallback=trap_lang,
        profile=principal,
    )
    assert [page.ref for page in misleading_args] == ["vff-hd"]

    explicit = select_pages_for_profile_attempt(
        list(DEMO_SERIES),
        profile=principal,
        title="Parcours Démo",
        year=2022,
        media_type="series",
        seasons={1},
    )
    assert [page.ref for page in explicit] == ["vff-hd"]

    crit = attempt_selection_criteria(principal)
    assert crit.quality_fallback != trap_csv
    assert crit.language_fallback != trap_lang


def test_principal_rejects_repli_only_candidate_until_explicit_repli():
    principal = _principal_profile()
    repli = _vostfr_profile()

    assert rank_pages_for_profile_attempt(DEMO_SERIES[1], profile=principal) is None
    assert rank_pages_for_profile_attempt(DEMO_SERIES[0], profile=principal) is not None

    assert select_pages_for_profile_attempt(
        list(DEMO_SERIES),
        profile=principal,
        title="Parcours Démo",
        year=2022,
        media_type="series",
        seasons={1},
    ) == [DEMO_SERIES[0]]

    repli_only = select_pages_for_profile_attempt(
        [DEMO_SERIES[1]],
        profile=repli,
        title="Parcours Démo",
        year=2022,
        media_type="series",
        seasons={1},
    )
    assert repli_only == [DEMO_SERIES[1]]


def test_reingest_adapter_uses_same_explicit_profile():
    principal = _principal_profile()
    repli = _vostfr_profile()
    media = Media(type="series", title="Parcours Démo", year=2022, pref_quality="VO", pref_language="EN")
    pages_principal = reingest_select_pages(list(DEMO_SERIES), media=media, seasons={1}, profile=principal)
    assert [page.ref for page in pages_principal] == ["vff-hd"]
    pages_repli = reingest_select_pages(
        list(DEMO_SERIES), media=media, seasons={1}, profile=repli, exclude=set()
    )
    assert [page.ref for page in pages_repli] == ["vost-1080"]


def test_manual_provider_link_always_ranks_before_profile_filter():
    from mediatracker.db.models import Provider

    profile = _principal_profile()
    manual_provider = Provider(id=7, kind="source", type="manual", name="Manual", enabled=True)
    manual_hd = Link(
        provider_id=manual_provider.id,
        provider=manual_provider,
        quality="HD",
        language="MULTI",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://manual",
        priority=1,
        filename="Exemple.Demo.2099.MULTi.HD.WEB.x264-GROUP.mkv",
        external_id="manual:hd",
    )
    assert rank_link_for_profile_attempt(manual_hd, profile=profile) == -1


def test_choose_link_for_profile_attempt_rejects_incompatible_version():
    principal = _principal_profile()
    repli = _vostfr_profile()
    vost_link = Link(
        quality="1080p",
        language="VOSTFR",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://x",
        priority=1,
    )
    vff_link = Link(quality="HD", language="VFF", hoster="rapidgator", kind="ddl_protected", url="http://y", priority=2)
    preference = ["rapidgator"]

    chosen_principal = choose_link_for_profile_attempt(
        preference,
        links=[vost_link, vff_link],
        profile=principal,
    )
    assert chosen_principal is vff_link

    chosen_repli = choose_link_for_profile_attempt(
        preference,
        links=[vost_link],
        profile=repli,
    )
    assert chosen_repli is vost_link


def test_choose_link_ignores_dead_link_even_when_listed_first_with_higher_priority():
    profile = _principal_profile()
    dead = Link(
        id=1,
        quality="HD",
        language="VFF",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://dead",
        priority=100,
        dead=True,
    )
    live = Link(
        id=2,
        quality="HD",
        language="VFF",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://live",
        priority=1,
        dead=False,
    )
    chosen = choose_link_for_profile_attempt(
        ["rapidgator"],
        links=[dead, live],
        profile=profile,
    )
    assert chosen is live


def test_choose_link_ignores_magnet_and_torrent_before_hoster_ddl():
    profile = _principal_profile()
    magnet = Link(
        id=10,
        quality="HD",
        language="VFF",
        hoster="",
        kind="magnet",
        url="magnet:?xt=urn:btih:" + "a" * 40,
        priority=100,
    )
    torrent = Link(
        id=11,
        quality="HD",
        language="VFF",
        hoster="",
        kind="torrent",
        url="http://torrent",
        priority=90,
    )
    ddl = Link(
        id=12,
        quality="HD",
        language="VFF",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://ddl",
        priority=1,
    )
    chosen = choose_link_for_profile_attempt(
        ["rapidgator"],
        links=[magnet, torrent, ddl],
        profile=profile,
    )
    assert chosen is ddl


def test_choose_link_skipped_id_still_excludes_live_hoster():
    profile = _principal_profile()
    skipped = Link(
        id=5,
        quality="HD",
        language="VFF",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://skip",
        priority=100,
    )
    other = Link(
        id=6,
        quality="1080p",
        language="VFF",
        hoster="rapidgator",
        kind="ddl_protected",
        url="http://other",
        priority=1,
    )
    chosen = choose_link_for_profile_attempt(
        ["rapidgator"],
        links=[skipped, other],
        profile=profile,
        skipped_link_ids={5},
    )
    assert chosen is other


def test_comet_explicit_context_ignores_media_pref_and_csv():
    principal = default_profile()
    explicit_profile, explicit_prefs = comet_context_for_attempt(profile=principal)
    assert explicit_profile is principal
    assert explicit_prefs.quality == "HD"
    assert explicit_prefs.language == "VFF"
    assert "VOSTFR" in explicit_prefs.language_fallback
    assert explicit_prefs.quality_fallback != ("SD", "4K")


def test_comet_streams_follow_explicit_repli_not_default():
    payload = json.loads((FIXTURES / "demo_building_s03e01.json").read_text(encoding="utf-8"))
    query = EpisodeQuery(
        title="Immeuble Démo",
        year=2021,
        media_type="series",
        imdb_id="tt9000001",
        season=3,
        episode=1,
    )
    repli = _vostfr_profile()
    chosen = select_comet_streams_for_profile_attempt(payload, query, profile=repli)
    assert chosen
    assert all(item.language == "VOSTFR" for item in chosen)


def test_contract_a_composition_principal_all_sources_before_repli(mock_hive_accepts):
    """Profil d'abord sur toutes les sources, puis repli ; Hive simulé opaque."""
    principal = _principal_profile()
    repli = _vostfr_profile()
    order = ("wawacity", "comet", "hive")

    def accepts(profile: Profile, source: str) -> bool:
        if profile.name == principal.name:
            return False
        if profile.name == repli.name and source == "comet":
            return rank_pages_for_profile_attempt(DEMO_SERIES[1], profile=profile) is not None
        return False

    found, trail = walk_profile_source_attempts((principal, repli), order, accepts=accepts)
    assert found is not None
    assert found.profile.name == repli.name
    assert found.source == "comet"
    assert len(trail) == 5
    assert [step.profile.name for step in trail] == [
        "Principal VFF",
        "Principal VFF",
        "Principal VFF",
        "VOSTFR 1080p",
        "VOSTFR 1080p",
    ]
    assert len(list(iter_profile_source_attempts((principal,), order))) == 3


def test_legacy_comet_context_defaults_when_media_prefs_unset(client, db):
    """Média non persisté : pref_quality/pref_language None → défauts SourcePreferences."""
    from tests.conftest import login

    login(client)
    settings = client.app.state.settings
    media = Media(type="film", title="Sans préférences")
    assert media.pref_quality is None
    assert media.pref_language is None

    _profile, prefs = legacy_comet_context(db, media, settings)
    assert prefs.quality == "HD"
    assert prefs.language == "VFF"


def test_legacy_comet_context_unchanged_without_explicit_profile(client, db):
    from tests.conftest import login

    login(client)
    settings = client.app.state.settings
    media = Media(
        type="series",
        title="Legacy Comet",
        pref_quality="1080p",
        pref_language="MULTI",
        allow_fallback=False,
    )
    db.add(media)
    db.commit()

    profile, prefs = legacy_comet_context(db, media, settings)
    from mediatracker.core.quality_profile import preferences_for, profile_for_media

    expected = profile_for_media(db, media)
    assert profile == expected
    assert expected is not None
    assert prefs == preferences_for(expected)


def test_adapters_do_not_embed_profile_chain_resolution():
    assert "resolve_profiles_for_media" not in open(
        Path(__file__).resolve().parents[1] / "backend/mediatracker/core/profile_attempt_adapters.py"
    ).read()


def test_non_wired_call_sites_inventory_documents_legacy_boundaries():
    joined = "\n".join(NON_WIRED_PROFILE_ATTEMPT_CALL_SITES)
    assert "_choose_link" in joined
    assert "unlock._from_named_source" not in joined
    assert "_ingest_other_versions" not in joined


@pytest.fixture
def mock_hive_accepts():
    yield

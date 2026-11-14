"""Lot C : sélection catalogue Hive pour un profil explicite (sans résolution fiche / CSV)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from tests.test_hive_t6 import _enable, _ruche

from mediatracker.core.profile_attempt import iter_profile_source_attempts, walk_profile_source_attempts
from mediatracker.core.profile_attempt_adapters import (
    NON_WIRED_PROFILE_ATTEMPT_CALL_SITES,
    pick_catalog_match_for_profile_attempt,
    rank_catalog_version_for_profile_attempt,
)
from mediatracker.core.quality_profile import Profile, builtin_profiles
from mediatracker.db.models import HiveCatalogue, HiveEntry, Media, MediaItem, QualityProfile
from mediatracker.hive.source_catalog import pick_catalog_match

NOW = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)


def _vff_principal() -> Profile:
    return Profile(
        name="Principal VFF",
        languages=("VFF",),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )


def _vostfr_repli() -> Profile:
    return next(p for p in builtin_profiles() if p.name == "VOSTFR 1080p")


def _seed_catalogue_row(
    db,
    *,
    cle: dict,
    version: str,
    sha: str,
    joignable: bool,
    ruche_hex: str,
    taille: int | None = None,
) -> None:
    from tests.conftest import PLAUSIBLE_VIDEO_BYTES

    if taille is None:
        taille = PLAUSIBLE_VIDEO_BYTES
    from mediatracker.hive.hive_catalogue import _stable_work_key
    sources = [
        {
            "ruche_id_hex": ruche_hex,
            "origine": "hv1test-test-test-test-test-test-test",
            "file_id_hex": "bb" * 16,
            "sha256": sha,
            "joignable": joignable,
        }
    ]
    db.add(
        HiveCatalogue(
            work_key=_stable_work_key(cle),
            version_key=version,
            cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            sources_json=json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            disponible=joignable,
            refreshed_at=NOW,
        )
    )
    if taille > 0:
        fiche = {"taille": taille, "type": cle.get("type"), "tmdb_id": cle.get("tmdb_id")}
        payload = {"taille": taille, "fiche": fiche}
        db.add(
            HiveEntry(
                ruche_id=bytes.fromhex(ruche_hex),
                origin_instance_id=sources[0]["origine"],
                seq=1,
                cle_json=json.dumps(cle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                fiche_json=json.dumps(fiche, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                sha256_hex=sha,
                file_id=bytes.fromhex(sources[0]["file_id_hex"]),
                etat="disponible",
                payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            )
        )
    db.commit()


@pytest.fixture
def db(client):
    _enable(client)
    _ruche(client)
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def ruche_hex(client) -> str:
    _enable(client)
    rid = _ruche(client)
    return rid.hex()


def test_principal_profile_picks_vff_not_vostfr(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9901, pref_quality="SD", pref_language="VOSTFR")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9901, "version": "1080p+VOSTFR"},
        version="1080p+VOSTFR",
        sha="a" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9901, "version": "HD+VFF"},
        version="HD+VFF",
        sha="b" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    principal = _vff_principal()
    match = pick_catalog_match_for_profile_attempt(db, media, item, profile=principal, prefer_available=True)
    assert match is not None
    assert match.sha256_hex == "b" * 64

    repli = _vostfr_repli()
    match_repli = pick_catalog_match_for_profile_attempt(db, media, item, profile=repli, prefer_available=True)
    assert match_repli is not None
    assert match_repli.sha256_hex == "a" * 64


def test_explicit_profile_ignores_pinned_and_default_on_media(db, ruche_hex):
    vost_row = QualityProfile(
        name="Épingle VOSTFR test",
        languages=["VOSTFR"],
        min_resolution=None,
        max_resolution="1080p",
        is_default=False,
    )
    db.add(vost_row)
    db.flush()
    media = Media(
        type="film",
        title="Film fictif",
        year=2099,
        tmdb_id=9902,
        pref_quality="SD",
        pref_language="VOSTFR",
        quality_profile_id=vost_row.id,
    )
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9902, "version": "1080p+VOSTFR"},
        version="1080p+VOSTFR",
        sha="c" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9902, "version": "HD+VFF"},
        version="HD+VFF",
        sha="d" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    explicit = _vff_principal()
    match = pick_catalog_match_for_profile_attempt(db, media, item, profile=explicit, prefer_available=True)
    assert match is not None
    assert match.sha256_hex == "d" * 64


def test_malformed_version_no_csv_fallback_on_explicit_path(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9903, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9903, "version": "UNKNOWNTOKEN"},
        version="UNKNOWNTOKEN",
        sha="e" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    principal = _vff_principal()
    assert pick_catalog_match_for_profile_attempt(db, media, item, profile=principal, prefer_available=True) is None
    # Chemin legacy inchangé : sans « Q+L » parsable, variant_rank ne peut pas élargir via CSV.
    legacy = pick_catalog_match(
        db,
        media,
        item,
        allow_fallback=True,
        quality_fallback=["HD", "1080p"],
        language_fallback=["VFF", "MULTI"],
        profile=principal,
        prefer_available=True,
    )
    assert legacy is None


def test_unknown_plus_unknown_kept_eligible_with_penalty(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9904, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9904, "version": "unknown+unknown"},
        version="unknown+unknown",
        sha="f" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    from mediatracker.hive.source_catalog import UNKNOWN_VERSION_RANK

    principal = _vff_principal()
    assert rank_catalog_version_for_profile_attempt("unknown+unknown", profile=principal) == UNKNOWN_VERSION_RANK
    assert pick_catalog_match_for_profile_attempt(db, media, item, profile=principal) is not None


def test_unavailable_penalty_preserved(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9905, pref_quality="HD", pref_language="VFF")
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9905, "version": "HD+VFF"},
        version="HD+VFF",
        sha="1" * 64,
        joignable=False,
        ruche_hex=ruche_hex,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9905, "version": "HD+VF"},
        version="HD+VF",
        sha="2" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
    )
    principal = Profile(
        name="VF large",
        languages=("VFF", "VF"),
        min_resolution=None,
        max_resolution=None,
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )
    match = pick_catalog_match_for_profile_attempt(db, media, item, profile=principal, prefer_available=True)
    assert match is not None
    assert match.sha256_hex == "2" * 64


def test_adapters_do_not_resolve_profile_chain():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "backend/mediatracker/core/profile_attempt_adapters.py").read_text()
    assert "resolve_profiles_for_media" not in source
    assert "iter_profiles_for_media" not in source


def test_contract_a_hive_accepts_after_remotes_fail():
    principal = _vff_principal()
    repli = _vostfr_repli()
    order = ("wawacity", "comet", "hive")

    def accepts(profile: Profile, source: str) -> bool:
        if source != "hive":
            return False
        if profile.name == principal.name:
            return False
        if profile.name == repli.name:
            return rank_catalog_version_for_profile_attempt("1080p+VOSTFR", profile=profile) is not None
        return False

    found, trail = walk_profile_source_attempts((principal, repli), order, accepts=accepts)
    assert found is not None
    assert found.source == "hive"
    assert found.profile.name == repli.name
    assert len(list(iter_profile_source_attempts((principal,), order))) == 3


def test_hive_poll_wired_via_orchestration():
    joined = "\n".join(NON_WIRED_PROFILE_ATTEMPT_CALL_SITES)
    assert "hive.source_poll" not in joined
    assert "hive.source_download" not in joined
    from pathlib import Path

    orchestration = (
        Path(__file__).resolve().parents[1] / "backend/mediatracker/core/profile_attempt_orchestration.py"
    ).read_text()
    assert "pick_hive_catalog_for_item" in orchestration


def _hd_vff_profile(**overrides) -> Profile:
    base = dict(
        name="HD VFF taille",
        languages=("VFF", "VF"),
        min_resolution=None,
        max_resolution=None,
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )
    base.update(overrides)
    return Profile(**base)


def test_size_rank_unknown_rejected_when_min_bound_only():
    profile = _hd_vff_profile(min_size_mb=100)
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=None) is None
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=0) is None


def test_size_rank_unknown_rejected_when_max_bound_only():
    profile = _hd_vff_profile(max_size_mb=500)
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=None) is None


def test_size_rank_unknown_rejected_when_both_bounds():
    profile = _hd_vff_profile(min_size_mb=100, max_size_mb=500)
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=None) is None


def test_size_rank_unknown_allowed_without_bounds():
    profile = _hd_vff_profile()
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=None) is not None


def test_size_rank_known_respects_min_and_max_mb():
    profile = _hd_vff_profile(min_size_mb=100, max_size_mb=500)
    too_small = 50 * 1024 * 1024
    ok = 200 * 1024 * 1024
    too_large = 600 * 1024 * 1024
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=too_small) is None
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=ok) is not None
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile, size=too_large) is None


def test_pick_rejects_catalogue_without_size_when_profile_has_bounds(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9910)
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9910, "version": "HD+VFF"},
        version="HD+VFF",
        sha="9" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=0,
    )
    profile = _hd_vff_profile(min_size_mb=1)
    assert pick_catalog_match_for_profile_attempt(db, media, item, profile=profile) is None


def test_pick_accepts_catalogue_without_size_when_no_bounds(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9911)
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9911, "version": "HD+VFF"},
        version="HD+VFF",
        sha="8" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=0,
    )
    profile = _hd_vff_profile()
    match = pick_catalog_match_for_profile_attempt(db, media, item, profile=profile)
    assert match is not None
    assert match.sha256_hex == "8" * 64


def test_pick_filters_by_stored_size_bounds(db, ruche_hex):
    media = Media(type="film", title="Film fictif", year=2099, tmdb_id=9912)
    item = MediaItem(media_id=0, season=None, episode=None, state="missing")
    db.add(media)
    db.flush()
    item.media_id = media.id
    db.add(item)
    db.commit()
    small_mb = 50
    ok_mb = 200
    large_mb = 900
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9912, "version": "HD+VFF"},
        version="HD+VFF",
        sha="7" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=small_mb * 1024 * 1024,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9912, "version": "HD+VF"},
        version="HD+VF",
        sha="6" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=ok_mb * 1024 * 1024,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9912, "version": "1080p+VFF"},
        version="1080p+VFF",
        sha="5" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        taille=large_mb * 1024 * 1024,
    )
    profile = _hd_vff_profile(min_size_mb=100, max_size_mb=500)
    match = pick_catalog_match_for_profile_attempt(db, media, item, profile=profile)
    assert match is not None
    assert match.sha256_hex == "6" * 64


def test_exclude_keywords_apply_to_version_key_only():
    profile = _hd_vff_profile(exclude_keywords=("CAM",))
    assert rank_catalog_version_for_profile_attempt("HD+VFF", profile=profile) is not None
    assert rank_catalog_version_for_profile_attempt("HD+CAM+VFF", profile=profile) is None

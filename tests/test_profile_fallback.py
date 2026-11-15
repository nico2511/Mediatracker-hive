"""Lot A : ordre global de replis profils, résolveur et contrat profil×source (sans consommateurs)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from tests.conftest import csrf, login

from mediatracker.core.profile_attempt import (
    iter_profile_source_attempts,
    walk_profile_source_attempts,
)
from mediatracker.core.profile_attempt_orchestration import ProfileChain, pick_remote_pages_for_profiles
from mediatracker.core.profile_fallback import (
    GLOBAL_ALLOW_FALLBACK_KEY,
    PROFILE_FALLBACK_ORDER_KEY,
    ProfileResolveError,
    encode_media_fallback_profile_ids,
    format_profile_fallback_order,
    parse_profile_fallback_order,
    poll_profile_repli_journal_note,
    read_profile_fallback_order,
    resolve_profiles_for_media,
)
from mediatracker.core.quality_profile import Profile
from mediatracker.db.models import Media, QualityProfile, Setting
from mediatracker.providers.base import SourceResult
from mediatracker.settings_store import SettingsError, update_settings


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _upsert_setting(db, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False))
    else:
        row.value = value
    db.commit()


def _profile_ids(client) -> dict[str, int]:
    rows = client.get("/api/v1/quality-profiles").json()["profiles"]
    return {row["name"]: row["id"] for row in rows}


def test_parse_and_format_profile_fallback_order_dedup_and_non_alpha():
    assert parse_profile_fallback_order("") == []
    assert parse_profile_fallback_order("7, 3, 7, 12") == [7, 3, 12]
    assert format_profile_fallback_order([7, 3, 12]) == "7,3,12"
    with pytest.raises(ValueError, match="invalide"):
        parse_profile_fallback_order("3,x,1")


def test_explicit_empty_order_ignores_nonempty_config_default(client, settings):
    login(client)
    ids = _profile_ids(client)
    ordered = [ids["4K Multi"], ids["720p VF"]]
    client.app.state.settings.profile_fallback_order = format_profile_fallback_order(ordered)

    seeded = client.patch(
        "/api/v1/settings",
        json={"values": {PROFILE_FALLBACK_ORDER_KEY: format_profile_fallback_order(ordered)}},
        headers=csrf(client),
    )
    assert seeded.status_code == 200, seeded.text

    cleared = client.patch(
        "/api/v1/settings",
        json={"values": {PROFILE_FALLBACK_ORDER_KEY: ""}},
        headers=csrf(client),
    )
    assert cleared.status_code == 200, cleared.text
    assert {item["key"]: item["value"] for item in cleared.json()["settings"]}[PROFILE_FALLBACK_ORDER_KEY] == ""

    db = client.app.state.session_factory()
    try:
        assert read_profile_fallback_order(db, client.app.state.settings) == []
        from mediatracker.core.quality_profile import ensure_default_profile

        ensure_default_profile(db)
        default_id = next(
            row.id for row in db.scalars(select(QualityProfile)).all() if row.is_default
        )
        _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")
        media = Media(type="film", title="Pellicule Vide", year=2020)
        db.add(media)
        db.commit()
        resolved = resolve_profiles_for_media(db, media, client.app.state.settings)
        assert [item.profile_id for item in resolved] == [default_id]
    finally:
        db.close()


def test_patch_invalid_syntax_returns_422_without_persisting_other_keys(client, settings):
    login(client)
    ids = _profile_ids(client)
    ordered = format_profile_fallback_order([ids["VOSTFR 1080p"], ids["Meilleure dispo"]])
    prior_ongoing = next(
        item["value"]
        for item in client.get("/api/v1/settings").json()["settings"]
        if item["key"] == "scan_ongoing_only"
    )

    ok = client.patch(
        "/api/v1/settings",
        json={"values": {PROFILE_FALLBACK_ORDER_KEY: ordered}},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text

    for bad in ("3,x,1", "0"):
        response = client.patch(
            "/api/v1/settings",
            json={
                "values": {
                    PROFILE_FALLBACK_ORDER_KEY: bad,
                    "scan_ongoing_only": not prior_ongoing,
                }
            },
            headers=csrf(client),
        )
        assert response.status_code == 422, (bad, response.text)

    fresh = client.app.state.session_factory()
    try:
        row = fresh.get(Setting, PROFILE_FALLBACK_ORDER_KEY)
        assert row is not None
        assert row.value == ordered
        ongoing = next(
            item
            for item in client.get("/api/v1/settings").json()["settings"]
            if item["key"] == "scan_ongoing_only"
        )
        assert ongoing["value"] is prior_ongoing
    finally:
        fresh.close()


def test_settings_roundtrip_and_reject_invalid_without_overwrite(client, settings):
    login(client)
    ids = _profile_ids(client)
    ordered = [ids["4K Multi"], ids["720p VF"], ids["VOSTFR 1080p"]]
    payload = format_profile_fallback_order(ordered)

    ok = client.patch(
        "/api/v1/settings",
        json={"values": {PROFILE_FALLBACK_ORDER_KEY: payload}},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text
    listed = {item["key"]: item["value"] for item in ok.json()["settings"]}
    assert listed[PROFILE_FALLBACK_ORDER_KEY] == payload

    db = client.app.state.session_factory()
    try:
        assert read_profile_fallback_order(db, settings) == ordered
    finally:
        db.close()

    bad = client.patch(
        "/api/v1/settings",
        json={"values": {PROFILE_FALLBACK_ORDER_KEY: "999991"}},
        headers=csrf(client),
    )
    assert bad.status_code == 422

    db = client.app.state.session_factory()
    try:
        assert read_profile_fallback_order(db, settings) == ordered
    finally:
        db.close()


def test_resolve_principal_pinned_vs_default(client, db, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db)
    rows = {row.name: row for row in db.scalars(select(QualityProfile)).all()}
    default_row = next(row for row in rows.values() if row.is_default)
    alt = rows["4K Multi"]

    media = Media(
        type="film",
        title="Pellicule Test",
        year=2020,
        pref_quality="HD",
        pref_language="VFF",
        allow_fallback=False,
    )
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    assert len(resolved) == 1
    assert resolved[0].profile_id == default_row.id
    assert resolved[0].is_principal

    media.quality_profile_id = alt.id
    db.commit()
    resolved = resolve_profiles_for_media(db, media, settings)
    assert resolved[0].profile_id == alt.id
    assert resolved[0].is_principal


def test_resolve_follows_global_allow_fallback_toggle(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    default_id = next(row.id for row in rows if row.is_default)
    others = [row.id for row in rows if row.id != default_id]
    order = format_profile_fallback_order([others[2], others[0], others[1]])

    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, order)
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="film", title="Pellicule Test B", year=2020)
    db.add(media)
    db.commit()

    off = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in off] == [default_id]

    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    on = resolve_profiles_for_media(db, media, settings)
    assert on[0].profile_id == default_id
    assert on[0].is_principal
    assert [item.profile_id for item in on[1:]] == [others[2], others[0], others[1]]


def test_resolve_drops_principal_and_duplicate_ids_from_global(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    default_id = next(row.id for row in rows if row.is_default)
    other_id = next(row.id for row in rows if not row.is_default)
    _upsert_setting(
        db,
        PROFILE_FALLBACK_ORDER_KEY,
        format_profile_fallback_order([default_id, other_id, other_id, default_id]),
    )
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")

    media = Media(type="film", title="Pellicule Test C", year=2020)
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in resolved] == [default_id, other_id]


def test_resolve_pinned_principal_strips_self_from_global_chain(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    pinned = rows[1]
    other = rows[2].id
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([pinned.id, other]))

    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    media = Media(
        type="film",
        title="Pellicule Test D",
        year=2020,
        quality_profile_id=pinned.id,
    )
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in resolved] == [pinned.id, other]


def test_resolve_errors_explicit_no_implicit_substitution(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    default_id = next(row.id for row in rows if row.is_default)

    media = Media(
        type="film",
        title="Pellicule Test E",
        year=2020,
        quality_profile_id=888_888,
    )
    db.add(media)
    db.commit()
    with pytest.raises(ProfileResolveError, match="épinglé"):
        resolve_profiles_for_media(db, media, settings)

    _upsert_setting(
        db,
        PROFILE_FALLBACK_ORDER_KEY,
        format_profile_fallback_order([default_id, 777_777]),
    )
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    media.quality_profile_id = None
    db.commit()
    with pytest.raises(ProfileResolveError, match="inconnu"):
        resolve_profiles_for_media(db, media, settings)

    for row in rows:
        if row.is_default:
            row.is_default = False
    db.commit()
    media2 = Media(type="film", title="Sans défaut", year=2020)
    db.add(media2)
    db.commit()
    with pytest.raises(ProfileResolveError, match="défaut"):
        resolve_profiles_for_media(db, media2, settings)


def test_profile_source_attempt_order_principal_before_repli_and_no_implicit_hive():
    principal = Profile(
        name="Principal",
        languages=("VFF",),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )
    repli1 = Profile(
        name="Repli Un",
        languages=("MULTI",),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )
    repli2 = Profile(
        name="Repli Deux",
        languages=("VOSTFR",),
        min_resolution=None,
        max_resolution="HD",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )
    order_a = ("wawacity", "comet", "hive")
    order_b = ("hive", "wawacity")

    attempts_a = list(iter_profile_source_attempts((principal, repli1, repli2), order_a))
    assert [(a.profile.name, a.source) for a in attempts_a] == [
        ("Principal", "wawacity"),
        ("Principal", "comet"),
        ("Principal", "hive"),
        ("Repli Un", "wawacity"),
        ("Repli Un", "comet"),
        ("Repli Un", "hive"),
        ("Repli Deux", "wawacity"),
        ("Repli Deux", "comet"),
        ("Repli Deux", "hive"),
    ]

    attempts_b = list(iter_profile_source_attempts((principal, repli1, repli2), order_b))
    assert [(a.profile.name, a.source) for a in attempts_b[:3]] == [
        ("Principal", "hive"),
        ("Principal", "wawacity"),
        ("Repli Un", "hive"),
    ]

    def accepts(profile: Profile, source: str) -> bool:
        return profile.name == "Repli Un" and source == "comet"

    found, trail = walk_profile_source_attempts(
        (principal, repli1, repli2),
        order_a,
        accepts=accepts,
    )
    assert found is not None
    assert found.profile.name == "Repli Un"
    assert found.source == "comet"
    assert len(trail) == 5
    assert all(step.profile.name == "Principal" for step in trail[:3])
    assert trail[-1].profile.name == "Repli Un"


def test_validate_profile_fallback_ids_via_settings_store(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    row = db.scalar(select(QualityProfile).limit(1))
    with pytest.raises(SettingsError, match="inconnu"):
        update_settings(db, settings, {PROFILE_FALLBACK_ORDER_KEY: format_profile_fallback_order([row.id, 424242])})
    stored = db.get(Setting, PROFILE_FALLBACK_ORDER_KEY)
    assert stored is None or (stored.value or "") == ""


def test_resolver_ignores_legacy_allow_fallback_column(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile, profile_for_media

    login(client)
    ensure_default_profile(db)
    repli = db.scalar(select(QualityProfile).where(QualityProfile.name == "720p VF"))
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli.id]))
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")

    media = Media(type="film", title="Legacy", year=2019, allow_fallback=False)
    db.add(media)
    db.commit()

    legacy = profile_for_media(db, media)
    resolved = resolve_profiles_for_media(db, media, settings)
    assert resolved[0].profile == legacy
    assert legacy is not None
    assert len(resolved) > 1
    media.fallback_profile_ids = ""
    db.commit()
    custom = resolve_profiles_for_media(db, media, settings)
    assert len(custom) == 1


def test_global_allow_fallback_enables_chain_when_fiche_off(client, db, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    default_id = next(row.id for row in rows if row.is_default)
    repli_id = next(row.id for row in rows if row.name == "720p VF")
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli_id]))
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")

    media = Media(type="series", title="Atelier Brume", year=2026, allow_fallback=False)
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in resolved] == [default_id, repli_id]


def test_both_fallbacks_off_yields_principal_only(client, db, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    default_id = next(row.id for row in rows if row.is_default)
    repli_id = next(row.id for row in rows if row.name == "720p VF")
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli_id]))
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="series", title="Atelier Brume", year=2026, allow_fallback=False)
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in resolved] == [default_id]


def test_pick_remote_pages_uses_fallback_profile_for_hd_only(client, db, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    repli_row = next(row for row in rows if row.name == "720p VF")
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli_row.id]))
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")

    media = Media(
        type="series",
        title="Parcours Labo",
        year=2026,
        allow_fallback=False,
    )
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    chain = ProfileChain(tuple(item.profile for item in resolved))
    hd_page = SourceResult(
        ref="s4-hd-vf",
        title="Parcours Labo",
        year=2026,
        media_type="series",
        season=4,
        quality="HD",
        language="VF",
        url="https://example.invalid/s4-hd",
    )
    pick = pick_remote_pages_for_profiles(
        [hd_page],
        media=media,
        seasons={4},
        chain=chain,
    )
    assert pick is not None
    assert pick.profile.name == "720p VF"
    assert pick.pages[0].quality == "HD"


def test_pick_remote_pages_principal_only_when_fallbacks_disabled(client, db, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    repli_row = next(row for row in rows if row.name == "720p VF")
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli_row.id]))
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "false")

    media = Media(type="series", title="Parcours Labo", year=2026, allow_fallback=False)
    db.add(media)
    db.commit()

    resolved = resolve_profiles_for_media(db, media, settings)
    chain = ProfileChain(tuple(item.profile for item in resolved))
    hd_page = SourceResult(
        ref="s4-hd-vf",
        title="Parcours Labo",
        year=2026,
        media_type="series",
        season=4,
        quality="HD",
        language="VF",
        url="https://example.invalid/s4-hd",
    )
    pick = pick_remote_pages_for_profiles(
        [hd_page],
        media=media,
        seasons={4},
        chain=chain,
    )
    assert pick is None


def test_custom_fallback_chain_two_profiles(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    default_id = next(row.id for row in rows if row.is_default)
    ids = [row.id for row in rows if row.id != default_id][:2]
    media = Media(
        type="film",
        title="Pellicule Perso",
        year=2020,
        fallback_profile_ids=encode_media_fallback_profile_ids(ids),
    )
    db.add(media)
    db.commit()
    resolved = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in resolved] == [default_id, *ids]


def test_custom_fallback_empty_is_principal_only(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    default_id = next(row.id for row in db.scalars(select(QualityProfile)).all() if row.is_default)
    media = Media(type="film", title="Pellicule Vide Perso", year=2020, fallback_profile_ids="")
    db.add(media)
    db.commit()
    resolved = resolve_profiles_for_media(db, media, settings)
    assert [item.profile_id for item in resolved] == [default_id]
    other = next(row.id for row in db.scalars(select(QualityProfile)).all() if row.id != default_id)
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([other]))
    assert len(resolve_profiles_for_media(db, media, settings)) == 1


def test_strict_1080p_principal_rejects_hd_720p_profile_accepts(client, db, settings):
    login(client)
    from mediatracker.core.quality_profile import ensure_default_profile

    ensure_default_profile(db)
    rows = list(db.scalars(select(QualityProfile)).all())
    repli_row = next(row for row in rows if row.name == "720p VF")
    strict_row = next(row for row in rows if row.name == "VOSTFR 1080p")
    _upsert_setting(db, GLOBAL_ALLOW_FALLBACK_KEY, "true")
    _upsert_setting(db, PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order([repli_row.id]))

    media = Media(
        type="series",
        title="Parcours Labo",
        year=2026,
        quality_profile_id=strict_row.id,
        fallback_profile_ids="",
    )
    db.add(media)
    db.commit()
    alone = ProfileChain(
        tuple(item.profile for item in resolve_profiles_for_media(db, media, settings)),
    )
    page_720 = SourceResult(
        ref="s4-720",
        title="Parcours Labo",
        year=2026,
        media_type="series",
        season=4,
        quality="720p",
        language="VF",
        url="https://example.invalid/s4-720",
    )
    assert pick_remote_pages_for_profiles([page_720], media=media, seasons={4}, chain=alone) is None

    media.fallback_profile_ids = None
    db.commit()
    chain = ProfileChain(tuple(item.profile for item in resolve_profiles_for_media(db, media, settings)))
    page_hd = SourceResult(
        ref="s4-hd",
        title="Parcours Labo",
        year=2026,
        media_type="series",
        season=4,
        quality="HD",
        language="VF",
        url="https://example.invalid/s4-hd",
    )
    pick = pick_remote_pages_for_profiles([page_hd], media=media, seasons={4}, chain=chain)
    assert pick is not None
    assert pick.profile.name == "720p VF"
    assert pick.profile_rank == 2


def test_media_api_validates_fallback_profile_ids(client, db):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    media = Media(type="film", title="Pellicule API", year=2020)
    db.add(media)
    db.commit()
    ok = client.patch(
        f"/api/v1/media/{media.id}",
        json={"fallback_profile_ids": []},
        headers=csrf(client),
    )
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["fallback_profile_ids"] == []

    row = next(row for row in db.scalars(select(QualityProfile)).all() if not row.is_default)
    assigned = client.patch(
        f"/api/v1/media/{media.id}",
        json={"fallback_profile_ids": [row.id]},
        headers=csrf(client),
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["fallback_profile_ids"] == [row.id]

    bad = client.patch(
        f"/api/v1/media/{media.id}",
        json={"fallback_profile_ids": [424242]},
        headers=csrf(client),
    )
    assert bad.status_code == 422

    back_global = client.patch(
        f"/api/v1/media/{media.id}",
        json={"fallback_profile_ids": None},
        headers=csrf(client),
    )
    assert back_global.status_code == 200
    assert back_global.json()["fallback_profile_ids"] is None


def test_poll_repli_journal_note():
    profile = Profile(
        name="Repli Essai",
        languages=("VF",),
        min_resolution=None,
        max_resolution="HD",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )
    assert poll_profile_repli_journal_note(profile, rank=1) == ""
    note = poll_profile_repli_journal_note(profile, rank=3)
    assert "Repli Essai" in note
    assert "rang 3" in note


def test_delete_profile_removes_id_from_global_and_media_fallback_chains(client, db, settings):
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    created = client.post(
        "/api/v1/quality-profiles",
        json={
            "name": "Profil Jetable",
            "languages": ["VFF"],
            "max_resolution": "HD",
            "max_size_mb": 2048,
        },
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    victim_id = created.json()["id"]
    keeper = next(row for row in db.scalars(select(QualityProfile)).all() if row.id != victim_id and not row.is_default)

    _upsert_setting(
        db,
        PROFILE_FALLBACK_ORDER_KEY,
        format_profile_fallback_order([victim_id, keeper.id]),
    )
    media = Media(
        type="film",
        title="Pellicule Purge",
        year=2020,
        fallback_profile_ids=format_profile_fallback_order([victim_id, keeper.id]),
    )
    db.add(media)
    db.commit()

    removed = client.delete(f"/api/v1/quality-profiles/{victim_id}", headers=csrf(client))
    assert removed.status_code == 204, removed.text

    db.expire_all()
    assert victim_id not in read_profile_fallback_order(db, settings)
    db.refresh(media)
    assert victim_id not in parse_profile_fallback_order(media.fallback_profile_ids or "")
    resolved = resolve_profiles_for_media(db, media, settings)
    assert all(item.profile_id != victim_id for item in resolved)

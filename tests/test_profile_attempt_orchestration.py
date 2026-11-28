"""Lot E : orchestration A×B/C (fausses sources, profils fictifs, traces PnSm)."""

# ruff: noqa: E402

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from tests.test_hive_profile_attempt import _seed_catalogue_row, _vff_principal, _vostfr_repli

pytest_plugins = ["tests.test_hive_profile_attempt"]  # noqa: E402

from mediatracker.core.profile_attempt import iter_profile_source_attempts
from mediatracker.core.profile_attempt_orchestration import (
    ProfileChain,
    download_source_order,
    load_profile_chain,
    pick_hive_catalog_for_item,
    profiles_for_poll_attempt,
    walk_profile_attempt_trail,
)
from mediatracker.core.profile_fallback import PROFILE_FALLBACK_ORDER_KEY, format_profile_fallback_order
from mediatracker.core.quality_profile import Profile
from mediatracker.db.models import Media, MediaItem, QualityProfile, Setting


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _p(name: str, langs: tuple[str, ...]) -> Profile:
    return Profile(
        name=name,
        languages=langs,
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )


def test_trace_p1s1_p1s2_p1hive_then_p2_order_a():
    p1, p2 = _p("P1", ("VFF",)), _p("P2", ("VOSTFR",))
    order = ("wawacity", "comet", "hive")
    attempts = iter_profile_source_attempts((p1, p2), order)
    labels = [f"P{a.profile_index + 1}S{order.index(a.source) + 1}" for a in attempts]
    assert labels == [
        "P1S1",
        "P1S2",
        "P1S3",
        "P2S1",
        "P2S2",
        "P2S3",
    ]


def test_trace_two_source_orders_hive_first():
    p1 = _p("P1", ("VFF",))
    order = ("hive", "wawacity")
    labels = [f"P1S{order.index(a.source) + 1}" for a in iter_profile_source_attempts((p1,), order)]
    assert labels == ["P1S1", "P1S2"]


def test_late_p1_beats_early_p2():
    p1, p2 = _p("P1", ("VFF",)), _p("P2", ("VOSTFR",))
    order = ("wawacity", "comet", "hive")

    def accepts(profile: Profile, source: str) -> bool:
        return profile.name == "P1" and source == "comet"

    trail = walk_profile_attempt_trail(ProfileChain((p1, p2)), order, accepts=accepts)
    assert trail.chosen is not None
    assert trail.chosen.profile.name == "P1"
    assert trail.chosen.source == "comet"
    assert trail.labels == ("P1S1", "P1S2")


def test_hive_absent_from_order_not_added(client, app, db, settings):
    from tests.conftest import connect_alldebrid_for_tests, login

    login(client)
    connect_alldebrid_for_tests(app)
    media = Media(type="film", title="Ordre fictif", year=2020, source_order="wawacity,comet")
    db.add(media)
    db.commit()
    order = download_source_order(db, media, app.state.settings)
    assert order == ("wawacity", "comet")
    assert "hive" not in order


def test_empty_fallback_list_principal_only(client, db, settings):
    from tests.conftest import login

    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    ensure_default_profile(db)
    default_row = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)))
    row = db.get(Setting, PROFILE_FALLBACK_ORDER_KEY)
    if row is None:
        db.add(Setting(key=PROFILE_FALLBACK_ORDER_KEY, value="", is_secret=False))
    else:
        row.value = ""
    media = Media(type="film", title="Repli off", year=2020, allow_fallback=False)
    db.add(media)
    db.commit()
    chain, err = profiles_for_poll_attempt(db, media, settings)
    assert err is None
    assert chain is not None
    assert len(chain.profiles) == 1
    assert default_row is not None
    assert chain.principal.name == default_row.name


def test_allow_fallback_expands_chain(client, db, settings):
    from tests.conftest import login

    login(client)
    rows = {row.name: row for row in db.scalars(select(QualityProfile)).all()}
    fallback_ids = [rows["VOSTFR 1080p"].id, rows["720p VF"].id]
    setting = db.get(Setting, PROFILE_FALLBACK_ORDER_KEY)
    ordered = format_profile_fallback_order(fallback_ids)
    if setting is None:
        db.add(Setting(key=PROFILE_FALLBACK_ORDER_KEY, value=ordered, is_secret=False))
    else:
        setting.value = ordered
    media = Media(
        type="film",
        title="Repli on",
        year=2021,
        fallback_profile_ids=ordered,
    )
    db.add(media)
    db.commit()
    chain = load_profile_chain(db, media, settings)
    assert len(chain.profiles) == 3


def test_frozen_hive_sha_skips_other_profile_match(db, ruche_hex):
    media = Media(type="film", title="Film figé", year=2099, tmdb_id=9901)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None, state="missing")
    db.add(item)
    db.commit()
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9901, "version": "HD+VFF"},
        version="HD+VFF",
        sha="f" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        file_id_hex="f0" * 16,
    )
    _seed_catalogue_row(
        db,
        cle={"type": "film", "tmdb_id": 9901, "version": "1080p+VOSTFR"},
        version="1080p+VOSTFR",
        sha="e" * 64,
        joignable=True,
        ruche_hex=ruche_hex,
        file_id_hex="e0" * 16,
    )
    chain = ProfileChain((_vff_principal(), _vostfr_repli()))
    pick = pick_hive_catalog_for_item(db, media, item, chain, frozen_sha256="f" * 64)
    assert pick is not None
    assert pick.match.sha256_hex == "f" * 64
    vost_only = pick_hive_catalog_for_item(
        db,
        media,
        item,
        ProfileChain((_vostfr_repli(),)),
        frozen_sha256="f" * 64,
    )
    assert vost_only is None


def test_non_wired_inventory_documents_legacy_boundaries_only():
    from mediatracker.core.profile_attempt_adapters import NON_WIRED_PROFILE_ATTEMPT_CALL_SITES

    joined = "\n".join(NON_WIRED_PROFILE_ATTEMPT_CALL_SITES)
    assert "_choose_link" in joined
    assert "unlock._from_named_source" not in joined
    assert "fiche_repair" not in joined
    assert "hive.source_poll" not in joined


def test_orchestrate_uses_worker_remote_poll_not_run_poll(monkeypatch, client, app):
    from datetime import datetime, timezone

    from tests.conftest import login

    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
    from mediatracker.core.source_poll import PollReport

    login(client)
    calls: list[str] = []
    factory = app.state.session_factory

    def fake_worker(*_a, **_k):
        calls.append("worker_remote_poll")
        return PollReport(
            result="found",
            message="ok",
            episodes=1,
            queued=1,
            proposed=0,
            last_check_at=datetime.now(timezone.utc),
            next_check_at=None,
        )

    chain = ProfileChain((_p("P1", ("VFF",)),))
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: chain,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity",),
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda _db, _settings, type_name, **_k: (1, MagicMock()) if type_name == "wawacity" else None,
    )
    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", fake_worker)
    monkeypatch.setattr("mediatracker.core.source_poll.close_source", lambda *_a, **_k: None)
    db = factory()
    try:
        media = Media(type="series", title="Spy", year=2020, followed=True, source_order="wawacity")
        db.add(media)
        db.commit()
        media_id = media.id
    finally:
        db.close()

    report = orchestrate_profile_first_poll(factory, app.state.settings, media_id, force=True)
    assert report is not None
    assert calls == ["worker_remote_poll"]


def test_orchestrate_advances_schedule_when_no_usable_source(client, app, monkeypatch):
    from datetime import date, datetime, timedelta, timezone

    from mediatracker.core.cleanup_classify import mark_classify_pass
    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.core.source_availability import filter_usable_source_order
    from mediatracker.core.source_poll import next_due
    from mediatracker.core.worker_schedule import compute_sleep_seconds, next_deadline

    NOW = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)
    factory = app.state.session_factory
    settings = app.state.settings
    cherche_calls: list[str] = []

    def _no_cherche(*_a, **_k):
        cherche_calls.append("miss")
        return None

    monkeypatch.setattr(
        "mediatracker.hive.signal_policy.maybe_publish_cherche_on_source_miss",
        _no_cherche,
    )
    db = factory()
    try:
        ensure_default_profile(db)
        usable = filter_usable_source_order(db, settings, ["wawacity", "comet", "hive"])
        assert usable == []
        media = Media(
            type="series",
            title="Serie sans source",
            year=2099,
            tmdb_id=88002,
            followed=True,
            new_episode_policy="auto",
            next_source_poll_at=NOW,
        )
        db.add(media)
        db.flush()
        db.add(
            MediaItem(
                media_id=media.id,
                season=1,
                episode=1,
                watched=False,
                state="missing",
                air_date=date(2026, 11, 7),
            )
        )
        db.commit()
        media_id = media.id
    finally:
        db.close()

    report = orchestrate_profile_first_poll(factory, settings, media_id, force=True, now=NOW)
    assert report is not None
    assert report.result == "nothing"
    assert report.message == "Aucune source configurée."
    assert cherche_calls == []

    db = factory()
    try:
        row = db.get(Media, media_id)
        nxt = row.next_source_poll_at
        assert nxt is not None
        if nxt.tzinfo is None:
            nxt = nxt.replace(tzinfo=timezone.utc)
        assert nxt > NOW
        mark_classify_pass(NOW)
        after = NOW + timedelta(seconds=1)
        assert next_due(db, settings, after, commit=False) is None
        deadline = next_deadline(db, settings, after)
        assert deadline is not None and deadline > after
        assert compute_sleep_seconds(db, settings, now=after) > 0.0
    finally:
        db.close()

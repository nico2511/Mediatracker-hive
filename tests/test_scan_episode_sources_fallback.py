"""Repli scan vers sources épisode quand le sondage « links » ne retient rien."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from tests.conftest import connect_alldebrid_for_tests, login

from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
from mediatracker.core.scan_episode_fallback import (
    _episode_sources_in_order,
    links_poll_retained_page,
    maybe_scan_episode_sources_fallback,
    scan_episode_sources_fallback_enabled,
)
from mediatracker.core.source_poll import PollReport
from mediatracker.db.models import Download, Media, MediaItem, Setting
from mediatracker.db.session import commit_retry

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def test_links_poll_retained_page_empty_search_false():
    report = PollReport(
        result="nothing",
        message="Aucun résultat sur les sources de recherche pour ce titre.",
        episodes=0,
        queued=0,
        proposed=0,
        last_check_at=NOW,
        next_check_at=NOW,
    )
    assert links_poll_retained_page(report) is False


def test_links_poll_retained_page_wrong_episodes_true():
    report = PollReport(
        result="nothing",
        message="La page ne contient pas les épisodes attendus.",
        episodes=0,
        queued=0,
        proposed=0,
        last_check_at=NOW,
        next_check_at=NOW,
    )
    assert links_poll_retained_page(report) is True


def test_scan_episode_sources_fallback_setting_default(client, app, db, settings):
    assert scan_episode_sources_fallback_enabled(db, settings) is True
    row = db.get(Setting, "scan_episode_sources_fallback")
    if row is None:
        db.add(Setting(key="scan_episode_sources_fallback", value="false", is_secret=False))
    else:
        row.value = "false"
    commit_retry(db)
    assert scan_episode_sources_fallback_enabled(db, settings) is False


def test_episode_source_order_respects_download_order(client, app, db, settings, monkeypatch):
    media = Media(type="series", title="Ordre repli fictif", year=2020, followed=True, tmdb_id=1, imdb_id="tt0000101")
    db.add(media)
    commit_retry(db)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.jobs._usable_download_sources",
        lambda *_a, **_k: ["wawacity", "torrentio", "comet"],
    )
    order = _episode_sources_in_order(db, media, settings)
    assert order == ("torrentio", "comet")


def test_orchestrate_triggers_episode_fallback_when_links_empty(client, app, db, settings, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    media = Media(
        type="series",
        title="Repli orchestré",
        year=2021,
        followed=True,
        new_episode_policy="auto",
        tmdb_id=2,
        imdb_id="tt0000102",
    )
    db.add(media)
    db.flush()
    db.add(
        MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=NOW.date() - timedelta(days=1),
            state="missing",
        )
    )
    commit_retry(db)

    fallback_calls: list[int] = []

    def fake_fallback(*_a, **_k):
        fallback_calls.append(1)
        return PollReport(
            result="found",
            message="1 épisode disponible, 1 mis en file. (repli comet).",
            episodes=1,
            queued=1,
            proposed=0,
            last_check_at=NOW,
            next_check_at=NOW,
        )

    class _LinksStub:
        capabilities = frozenset({"links"})

    monkeypatch.setattr("mediatracker.providers.sources.registry.for_type", lambda _n: _LinksStub())
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback.maybe_scan_episode_sources_fallback",
        fake_fallback,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity", "comet"),
    )
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda *_a, **_k: (1, MagicMock(close=lambda: None)),
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    def worker_empty(*_a, **_k):
        return PollReport(
            result="nothing",
            message="Aucun résultat sur les sources de recherche pour ce titre.",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=NOW,
            next_check_at=NOW,
        )

    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", worker_empty)

    report = orchestrate_profile_first_poll(
        app.state.session_factory,
        settings,
        media.id,
        force=True,
        now=NOW,
    )
    assert len(fallback_calls) == 1
    assert report is not None and report.queued == 1


def test_orchestrate_skips_episode_fallback_when_links_retained_page(client, app, db, settings, monkeypatch):
    login(client)
    media = Media(type="film", title="Page retenue", year=2020, followed=True)
    db.add(media)
    commit_retry(db)

    fallback_calls: list[int] = []

    def fake_fallback(*_a, **_k):
        fallback_calls.append(1)
        return None

    class _LinksStub:
        capabilities = frozenset({"links"})

    monkeypatch.setattr("mediatracker.providers.sources.registry.for_type", lambda _n: _LinksStub())
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback.maybe_scan_episode_sources_fallback",
        fake_fallback,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity",),
    )
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda *_a, **_k: (1, MagicMock(close=lambda: None)),
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    def worker_wrong_episode(*_a, **_k):
        return PollReport(
            result="nothing",
            message="La page ne contient pas le film attendu.",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=NOW,
            next_check_at=NOW,
        )

    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", worker_wrong_episode)

    orchestrate_profile_first_poll(app.state.session_factory, settings, media.id, force=True, now=NOW)
    assert fallback_calls == []


def test_orchestrate_skips_fallback_when_setting_disabled(client, app, db, settings, monkeypatch):
    login(client)
    row = db.get(Setting, "scan_episode_sources_fallback")
    if row is None:
        db.add(Setting(key="scan_episode_sources_fallback", value="false", is_secret=False))
    else:
        row.value = "false"
    media = Media(type="film", title="Repli off", year=2019, followed=True, imdb_id="tt0000103")
    db.add(media)
    commit_retry(db)

    inner_calls: list[int] = []

    def fake_maybe(*_a, **_k):
        inner_calls.append(1)
        return None

    class _LinksStub:
        capabilities = frozenset({"links"})

    monkeypatch.setattr("mediatracker.providers.sources.registry.for_type", lambda _n: _LinksStub())
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback.maybe_scan_episode_sources_fallback",
        fake_maybe,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity",),
    )
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda *_a, **_k: (1, MagicMock(close=lambda: None)),
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)

    def worker_empty(*_a, **_k):
        return PollReport(
            result="nothing",
            message="Aucun résultat sur les sources de recherche pour ce titre.",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=NOW,
            next_check_at=NOW,
        )

    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", worker_empty)

    report = orchestrate_profile_first_poll(app.state.session_factory, settings, media.id, force=True, now=NOW)
    assert inner_calls == []
    assert report is not None
    assert "Aucun résultat" in (report.message or "")


def test_maybe_scan_respects_remote_search_cooldown(client, app, db, settings, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain

    media = Media(
        type="series",
        title="Cooldown fictif",
        year=2022,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000104",
        tmdb_id=4,
        last_remote_search_at=NOW - timedelta(minutes=5),
    )
    db.add(media)
    db.flush()
    db.add(
        MediaItem(
            media_id=media.id,
            season=1,
            episode=2,
            air_date=NOW.date() - timedelta(days=2),
            state="missing",
        )
    )
    commit_retry(db)

    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._fetch_episode_candidates",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("network")),
    )
    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media.id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is None


def test_maybe_scan_skips_episode_already_present(client, app, db, settings, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.providers.base import LinkCandidate

    media = Media(
        type="series",
        title="Présent local",
        year=2023,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000105",
        tmdb_id=5,
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=1,
        episode=3,
        air_date=NOW.date() - timedelta(days=1),
        state="missing",
        downloaded=True,
    )
    db.add(item)
    commit_retry(db)

    fetch_calls: list[int] = []

    def fake_fetch(*_a, **_k):
        fetch_calls.append(1)
        return [
            LinkCandidate(
                url="magnet:?xt=urn:btih:" + "a" * 40,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="x",
            )
        ]

    monkeypatch.setattr("mediatracker.core.scan_episode_fallback._fetch_episode_candidates", fake_fetch)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media.id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is None
    assert fetch_calls == []


def test_maybe_scan_queues_cached_magnet(client, app, db, settings, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.providers.base import LinkCandidate
    from mediatracker.providers.debrid.alldebrid import MagnetCacheHit

    ensure_default_profile(db)

    media = Media(
        type="series",
        title="Flux cache fictif",
        year=2024,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000106",
        tmdb_id=6,
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=2,
        episode=1,
        air_date=NOW.date() - timedelta(days=1),
        state="missing",
    )
    db.add(item)
    commit_retry(db)
    media_id = media.id
    item_id = item.id

    magnet = "magnet:?xt=urn:btih:" + "f" * 40

    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._episode_sources_in_order",
        lambda *_a, **_k: ("comet",),
    )
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._fetch_episode_candidates",
        lambda *_a, **_k: [
            LinkCandidate(
                url=magnet,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="comet:" + "f" * 40 + ":0",
            )
        ],
    )

    class _Debrid:
        def magnet_cache(self, magnets):
            return [MagnetCacheHit(magnet=magnets[0], cached=True, name="demo.mkv")]

    monkeypatch.setattr(
        "mediatracker.core.pipeline.clients._debrid",
        lambda *_a, **_k: (None, _Debrid()),
    )
    monkeypatch.setattr("mediatracker.core.source_availability.source_usable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._source_unavailable_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media_id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is not None and report.queued == 1
    assert "repli comet" in (report.message or "")
    db2 = app.state.session_factory()
    try:
        dl = db2.scalar(select(Download).where(Download.media_item_id == item_id))
        assert dl is not None and dl.state == "pending"
    finally:
        db2.close()


def test_maybe_scan_rejects_uncached_magnet(client, app, db, settings, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.providers.base import LinkCandidate
    from mediatracker.providers.debrid.alldebrid import MagnetCacheHit

    ensure_default_profile(db)

    media = Media(
        type="series",
        title="Flux hors cache fictif",
        year=2024,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000107",
        tmdb_id=7,
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=2,
        episode=2,
        air_date=NOW.date() - timedelta(days=1),
        state="missing",
    )
    db.add(item)
    commit_retry(db)
    media_id = media.id
    item_id = item.id

    magnet = "magnet:?xt=urn:btih:" + "e" * 40

    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._episode_sources_in_order",
        lambda *_a, **_k: ("comet",),
    )
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._fetch_episode_candidates",
        lambda *_a, **_k: [
            LinkCandidate(
                url=magnet,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="comet:" + "e" * 40 + ":0",
            )
        ],
    )

    class _Debrid:
        def magnet_cache(self, magnets):
            return [MagnetCacheHit(magnet=magnets[0], cached=False, name="demo.mkv")]

    monkeypatch.setattr(
        "mediatracker.core.pipeline.clients._debrid",
        lambda *_a, **_k: (None, _Debrid()),
    )
    monkeypatch.setattr("mediatracker.core.source_availability.source_usable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._source_unavailable_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media_id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is None
    db2 = app.state.session_factory()
    try:
        dl = db2.scalar(select(Download).where(Download.media_item_id == item_id))
        assert dl is None
    finally:
        db2.close()


def test_maybe_scan_reuses_remote_search_slot_same_pass(client, app, db, settings, monkeypatch):
    """Le repli doit pouvoir tourner quand le créneau a déjà été pris au même instant."""
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.providers.base import LinkCandidate

    ensure_default_profile(db)

    media = Media(
        type="series",
        title="Créneau même passage",
        year=2025,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000201",
        tmdb_id=21,
        last_remote_search_at=NOW,
    )
    db.add(media)
    db.flush()
    db.add(
        MediaItem(
            media_id=media.id,
            season=1,
            episode=1,
            air_date=NOW.date() - timedelta(days=1),
            state="missing",
        )
    )
    commit_retry(db)

    fetch_calls: list[int] = []
    magnet = "magnet:?xt=urn:btih:" + "b" * 40

    def fake_fetch(*_a, **_k):
        fetch_calls.append(1)
        return [
            LinkCandidate(
                url=magnet,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="comet:" + "b" * 40 + ":0",
            )
        ]

    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._episode_sources_in_order",
        lambda *_a, **_k: ("comet",),
    )
    monkeypatch.setattr("mediatracker.core.scan_episode_fallback._fetch_episode_candidates", fake_fetch)

    class _Debrid:
        def magnet_cache(self, magnets):
            from mediatracker.providers.debrid.alldebrid import MagnetCacheHit

            return [MagnetCacheHit(magnet=magnets[0], cached=True, name="demo.mkv")]

    monkeypatch.setattr("mediatracker.core.pipeline.clients._debrid", lambda *_a, **_k: (None, _Debrid()))
    monkeypatch.setattr("mediatracker.core.source_availability.source_usable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._source_unavailable_reason",
        lambda *_a, **_k: None,
    )

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media.id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is not None and report.queued == 1
    assert "repli comet" in (report.message or "")
    assert fetch_calls == [1]


def test_maybe_scan_next_episode_slot_persists_item(client, app, db, settings, monkeypatch):
    """Slot « prochain épisode » sans ligne MediaItem : création à l'apply, pas en session plan fermée."""
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.db.models import Link
    from mediatracker.providers.base import LinkCandidate

    ensure_default_profile(db)

    media = Media(
        type="series",
        title="Prochain épisode fictif",
        year=2025,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000202",
        tmdb_id=22,
        next_episode_season=3,
        next_episode_number=1,
        next_air_date=NOW.date() - timedelta(days=1),
    )
    db.add(media)
    commit_retry(db)

    magnet = "magnet:?xt=urn:btih:" + "c" * 40
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._episode_sources_in_order",
        lambda *_a, **_k: ("comet",),
    )
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._fetch_episode_candidates",
        lambda *_a, **_k: [
            LinkCandidate(
                url=magnet,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="comet:" + "c" * 40 + ":0",
            )
        ],
    )

    class _Debrid:
        def magnet_cache(self, magnets):
            from mediatracker.providers.debrid.alldebrid import MagnetCacheHit

            return [MagnetCacheHit(magnet=magnets[0], cached=True, name="demo.mkv")]

    monkeypatch.setattr("mediatracker.core.pipeline.clients._debrid", lambda *_a, **_k: (None, _Debrid()))
    monkeypatch.setattr("mediatracker.core.source_availability.source_usable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._source_unavailable_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media.id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is not None and report.queued == 1
    assert "repli comet" in (report.message or "")
    db2 = app.state.session_factory()
    try:
        item = db2.scalar(
            select(MediaItem).where(
                MediaItem.media_id == media.id,
                MediaItem.season == 3,
                MediaItem.episode == 1,
            )
        )
        assert item is not None
        link = db2.scalar(select(Link).where(Link.media_item_id == item.id))
        assert link is not None
    finally:
        db2.close()


def test_maybe_scan_propose_policy_stores_link_without_queue(client, app, db, settings, monkeypatch):
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.db.models import Link
    from mediatracker.providers.base import LinkCandidate

    ensure_default_profile(db)

    media = Media(
        type="series",
        title="Politique proposer fictive",
        year=2025,
        followed=True,
        new_episode_policy="propose",
        imdb_id="tt0000203",
        tmdb_id=23,
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=1,
        episode=4,
        air_date=NOW.date() - timedelta(days=1),
        state="missing",
    )
    db.add(item)
    commit_retry(db)
    item_id = item.id

    magnet = "magnet:?xt=urn:btih:" + "d" * 40
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._episode_sources_in_order",
        lambda *_a, **_k: ("comet",),
    )
    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._fetch_episode_candidates",
        lambda *_a, **_k: [
            LinkCandidate(
                url=magnet,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="comet:" + "d" * 40 + ":0",
            )
        ],
    )

    class _Debrid:
        def magnet_cache(self, magnets):
            from mediatracker.providers.debrid.alldebrid import MagnetCacheHit

            return [MagnetCacheHit(magnet=magnets[0], cached=True, name="demo.mkv")]

    monkeypatch.setattr("mediatracker.core.pipeline.clients._debrid", lambda *_a, **_k: (None, _Debrid()))
    monkeypatch.setattr("mediatracker.core.source_availability.source_usable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._source_unavailable_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media.id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert report is not None and report.queued == 0 and report.proposed == 1
    db2 = app.state.session_factory()
    try:
        dl = db2.scalar(select(Download).where(Download.media_item_id == item_id))
        link = db2.scalar(select(Link).where(Link.media_item_id == item_id))
        assert dl is None
        assert link is not None
    finally:
        db2.close()


def test_maybe_scan_pending_download_still_fetches_no_duplicate(client, app, db, settings, monkeypatch):
    """Job déjà en file (rediscover) : interroger les sources, ne pas recréer de Download."""
    login(client)
    connect_alldebrid_for_tests(app)
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.providers.base import LinkCandidate

    ensure_default_profile(db)

    media = Media(
        type="series",
        title="File existante fictive",
        year=2025,
        followed=True,
        new_episode_policy="auto",
        imdb_id="tt0000204",
        tmdb_id=24,
    )
    db.add(media)
    db.flush()
    item = MediaItem(
        media_id=media.id,
        season=2,
        episode=3,
        air_date=NOW.date() - timedelta(days=1),
        state="missing",
    )
    db.add(item)
    db.flush()
    existing = Download(media_item_id=item.id, state="pending", created_at=NOW)
    db.add(existing)
    commit_retry(db)
    item_id = item.id

    fetch_calls: list[int] = []
    magnet = "magnet:?xt=urn:btih:" + "9" * 40

    def fake_fetch(*_a, **_k):
        fetch_calls.append(1)
        return [
            LinkCandidate(
                url=magnet,
                kind="magnet",
                hoster=None,
                quality="1080p",
                language="VFF",
                external_id="comet:" + "9" * 40 + ":0",
            )
        ]

    monkeypatch.setattr(
        "mediatracker.core.scan_episode_fallback._episode_sources_in_order",
        lambda *_a, **_k: ("comet",),
    )
    monkeypatch.setattr("mediatracker.core.scan_episode_fallback._fetch_episode_candidates", fake_fetch)

    class _Debrid:
        def magnet_cache(self, magnets):
            from mediatracker.providers.debrid.alldebrid import MagnetCacheHit

            return [MagnetCacheHit(magnet=magnets[0], cached=True, name="demo.mkv")]

    monkeypatch.setattr("mediatracker.core.pipeline.clients._debrid", lambda *_a, **_k: (None, _Debrid()))
    monkeypatch.setattr("mediatracker.core.source_availability.source_usable", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.unlock._source_unavailable_reason",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    chain = load_profile_chain(db, media, settings)
    report = maybe_scan_episode_sources_fallback(
        app.state.session_factory,
        settings,
        media.id,
        chain,
        moment=NOW,
        force=True,
        rng=MagicMock(),
        links_retained_any_page=False,
        chosen=None,
    )
    assert fetch_calls == [1]
    assert report is not None and report.queued == 0 and report.proposed == 1
    db2 = app.state.session_factory()
    try:
        rows = list(db2.scalars(select(Download).where(Download.media_item_id == item_id)).all())
        assert len(rows) == 1 and rows[0].id == existing.id
    finally:
        db2.close()

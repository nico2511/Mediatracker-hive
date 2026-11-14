"""Lot E — régressions actives (gate, HTTP/retry, priorité file, Hive scoped)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from tests.conftest import connect_alldebrid_for_tests, login

from mediatracker.core.profile_attempt_orchestration import (
    ProfileChain,
    active_hive_fetch_sha_for_item,
    orchestrate_profile_first_poll,
    profiles_for_poll_attempt,
)
from mediatracker.core.quality_profile import Profile
from mediatracker.core.source_poll_phased import network_call_count, reset_network_call_count
from mediatracker.db import session as session_mod
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    Link,
    Media,
    MediaItem,
    Provider,
    QualityProfile,
)
from mediatracker.db.session import commit_retry
from mediatracker.providers.base import LinkCandidate, SourceResult

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


class _EmptyThenFullSource:
    capabilities = frozenset({"links"})
    type = "two-phase-mock"
    call = 0

    def search(self, *_a, **_k) -> list:
        type(self).call += 1
        if type(self).call == 1:
            return [
                SourceResult(
                    title="Série fictive",
                    year=2023,
                    ref="empty",
                    url="/empty",
                    quality="HD",
                    language="VOSTFR",
                    season=1,
                )
            ]
        return [
            SourceResult(
                title="Série fictive",
                year=2023,
                ref="good",
                url="/good",
                quality="HD",
                language="VFF",
                season=1,
                episode=1,
            )
        ]

    def get_links(self, ref) -> list:
        if ref.ref == "good":
            return [
                LinkCandidate(
                    url="https://exemple.test/fichier",
                    hoster="rapidgator",
                    quality="HD",
                    language="VFF",
                    season=1,
                    episode=1,
                )
            ]
        return []

    def close(self) -> None:
        return None


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def _principal_profile() -> Profile:
    return Profile(
        name="Principal VFF",
        languages=("VFF", "VF"),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
    )


def test_finish_deferred_schedule_preserves_next_poll(client, app, db):
    import random

    from mediatracker.core.source_poll import _finish

    login(client)
    media = Media(type="series", title="Schedule", year=2023, followed=True)
    db.add(media)
    db.commit()
    future = NOW.replace(tzinfo=None)
    media.next_source_poll_at = future
    db.commit()
    _finish(
        db,
        media,
        client.app.state.settings,
        NOW,
        random.Random(0),
        result="nothing",
        message="vide",
        episodes=0,
        queued=0,
        proposed=0,
        failed=False,
        schedule=False,
    )
    db.commit()
    db.refresh(media)
    assert media.next_source_poll_at == future


class _VostfrOnlySource:
    capabilities = frozenset({"links"})
    type = "mock-vostfr"

    def search(self, *_a, **_k) -> list:
        return [
            SourceResult(
                title="Série fictive",
                year=2023,
                ref="vost",
                url="/vost",
                quality="HD",
                language="VOSTFR",
                season=1,
                episode=1,
            )
        ]

    def get_links(self, *_a, **_k) -> list:
        return []

    def close(self) -> None:
        return None


def test_orchestrate_s1_empty_s2_reaches_network_force_false(client, app, monkeypatch):
    """S1 sans candidat profil principal ; S2 ouverte et phase réseau réelle (sans force=True)."""
    from mediatracker.core.quality_profile import ensure_default_profile

    login(client)
    db = app.state.session_factory()
    try:
        ensure_default_profile(db)
        media = Media(
            type="series",
            title="Série gate",
            year=2023,
            followed=True,
            tmdb_id=88001,
            new_episode_policy="auto",
            last_source_poll_at=(NOW.replace(tzinfo=None)),
            next_source_poll_at=NOW.replace(tzinfo=None),
            catch_up_pending=True,
        )
        db.add(media)
        db.flush()
        db.add(
            MediaItem(
                media_id=media.id,
                season=1,
                episode=1,
                state="missing",
                air_date=NOW.date(),
            )
        )
        commit_retry(db)
        media_id = media.id
        future = media.next_source_poll_at
    finally:
        db.close()

    opened: list[str] = []
    src_a = _VostfrOnlySource()
    _EmptyThenFullSource.call = 0
    src_b = _EmptyThenFullSource()

    def open_remote(db, settings, type_name, **_k):
        if type_name == "mock_a":
            opened.append("mock_a")
            return 101, src_a
        if type_name == "mock_b":
            opened.append("mock_b")
            return 102, src_b
        return None

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("mock_a", "mock_b"),
    )
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr(
        "mediatracker.core.source_binding.pick_bound_pages",
        lambda *_a, **_k: None,
    )

    reset_network_call_count()
    report = orchestrate_profile_first_poll(
        app.state.session_factory,
        app.state.settings,
        media_id,
        force=False,
        now=NOW,
    )
    assert report is not None
    assert opened == ["mock_a", "mock_b"]
    assert network_call_count() == 2
    db2 = app.state.session_factory()
    try:
        row = db2.get(Media, media_id)
        assert row is not None
        assert row.next_source_poll_at is not None
        assert row.next_source_poll_at != future
    finally:
        db2.close()


def test_orchestrate_apply_retry_does_not_replay_http(client, app, monkeypatch):
    import threading

    from tests.test_source_poll_sqlite_release import _prepare_followed_series, _slow_handler
    from tests.test_wawacity import _source

    from mediatracker.db.models import Provider
    from mediatracker.providers.sources.wawacity import HtmlCache

    connect_alldebrid_for_tests(app)
    media_id = _prepare_followed_series(client, app)
    factory = app.state.session_factory
    settings = app.state.settings
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate, delay=0.05)
    source = _source(handler, cache=HtmlCache(0))
    prep = factory()
    try:
        provider_id = prep.query(Provider).filter(Provider.type == "wawacity").one().id
    finally:
        prep.close()

    reset_network_call_count()
    assert network_call_count() == 0
    real_commit = session_mod.commit_retry
    real_apply = session_mod.apply_with_retry
    active_label: dict[str, str | None] = {"v": None}
    fail_once: dict[str, int] = {
        "Application sondage source": 1,
        "Finalisation sondage profils×sources": 1,
    }

    def flaky_commit(db, *, attempts=3):
        label = active_label["v"]
        if label and fail_once.get(label, 0) > 0:
            fail_once[label] -= 1
            raise OperationalError("database is locked", {}, None)
        return real_commit(db, attempts=attempts)

    def tracked_apply(factory, label, fn, *, attempts=3):
        active_label["v"] = label
        try:
            return real_apply(factory, label, fn, attempts=attempts)
        finally:
            active_label["v"] = None

    def open_remote(db, settings, type_name, **_k):
        if type_name != "wawacity":
            return None
        return provider_id, source

    monkeypatch.setattr(session_mod, "commit_retry", flaky_commit)
    monkeypatch.setattr(session_mod, "apply_with_retry", tracked_apply)
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    try:
        orchestrate_profile_first_poll(factory, settings, media_id, force=True, now=NOW)
    finally:
        source.close()
    assert network_gate.is_set()
    assert fail_once["Application sondage source"] == 0
    assert fail_once["Finalisation sondage profils×sources"] == 0
    assert network_call_count() == 2


def _seed_active_hive_download(db, item: MediaItem, sha: str, *, state: str = "downloading") -> None:
    from mediatracker.db.models import DownloadHiveFetch

    row = Download(media_item_id=item.id, state=state)
    db.add(row)
    db.flush()
    db.add(
        DownloadHiveFetch(
            download_id=row.id,
            sha256_hex=sha,
            sources_json="[]",
            ruche_id=b"\xaa" * 16,
            file_id=b"\xbb" * 16,
            origin_instance_id="hv1x",
            peer_base_url="https://peer.exemple.test",
            expected_size=100,
            updated_at=NOW,
        )
    )
    db.flush()


def test_hive_sha_rejects_ambiguous_or_wrong_type(db):
    media_film = Media(type="film", title="Film", tmdb_id=9001)
    media_series = Media(type="series", title="Série", tmdb_id=9001)
    db.add(media_film)
    db.add(media_series)
    db.flush()
    item_film = MediaItem(media_id=media_film.id, state="missing")
    item_series = MediaItem(media_id=media_series.id, season=1, episode=1, state="missing")
    db.add(item_film)
    db.add(item_series)
    db.flush()
    _seed_active_hive_download(db, item_series, "c" * 64)
    db.commit()
    assert active_hive_fetch_sha_for_item(db, media_film, item_film) is None
    assert active_hive_fetch_sha_for_item(db, media_series, item_series) == "c" * 64


def test_hive_sha_two_series_same_slot_different_tmdb(db):
    media_a = Media(type="series", title="Série A", tmdb_id=8101)
    media_b = Media(type="series", title="Série B", tmdb_id=8102)
    db.add(media_a)
    db.add(media_b)
    db.flush()
    item_a = MediaItem(media_id=media_a.id, season=2, episode=3, state="missing")
    item_b = MediaItem(media_id=media_b.id, season=2, episode=3, state="missing")
    db.add(item_a)
    db.add(item_b)
    db.flush()
    _seed_active_hive_download(db, item_b, "e" * 64, state="pending")
    db.commit()
    assert active_hive_fetch_sha_for_item(db, media_a, item_a) is None
    assert active_hive_fetch_sha_for_item(db, media_b, item_b) == "e" * 64


def test_hive_sha_scoped_to_media_item_not_adjacent_slot(db):
    media = Media(type="series", title="Série slot", tmdb_id=8200)
    db.add(media)
    db.flush()
    item_other = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    item_target = MediaItem(media_id=media.id, season=1, episode=2, state="missing")
    db.add_all([item_other, item_target])
    db.flush()
    _seed_active_hive_download(db, item_other, "f" * 64)
    db.commit()
    assert active_hive_fetch_sha_for_item(db, media, item_target) is None
    assert active_hive_fetch_sha_for_item(db, media, item_other) == "f" * 64


def test_active_hive_sha_scoped_per_tmdb_not_global(db):
    media_a = Media(type="film", title="Film A", tmdb_id=1001)
    media_b = Media(type="film", title="Film B", tmdb_id=1002)
    db.add(media_a)
    db.add(media_b)
    db.flush()
    item_a = MediaItem(media_id=media_a.id, state="missing")
    item_b = MediaItem(media_id=media_b.id, state="missing")
    db.add(item_a)
    db.add(item_b)
    db.flush()
    _seed_active_hive_download(db, item_b, "a" * 64)
    db.commit()
    assert active_hive_fetch_sha_for_item(db, media_a, item_a) is None
    assert active_hive_fetch_sha_for_item(db, media_b, item_b) == "a" * 64


def test_single_profile_skips_broken_global_chain(db, settings):
    media = Media(
        type="film",
        title="Legacy",
        year=2020,
        allow_fallback=True,
        quality_profile_id=999999,
    )
    db.add(media)
    db.commit()
    explicit = Profile(
        name="Épinglé explicite",
        languages=("VFF", "VF"),
        min_resolution=None,
        max_resolution="1080p",
        min_size_mb=None,
        max_size_mb=None,
        exclude_keywords=(),
        quality_profile_id=4242,
    )
    chain, err = profiles_for_poll_attempt(db, media, settings, single_profile=explicit)
    assert err is None
    assert chain is not None
    assert chain.profiles == (explicit,)


def test_hive_sha_absent_without_active_download(db):
    media = Media(type="film", title="Film sans file", tmdb_id=8300)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.commit()
    assert active_hive_fetch_sha_for_item(db, media, item) is None


def test_resolve_poll_profile_missing_explicit_not_principal(db, settings):
    from mediatracker.core.pipeline import unlock as unlock_mod
    from mediatracker.core.profile_attempt_orchestration import (
        POLL_PROFILE_MISSING,
        resolve_download_poll_profile,
    )

    media = Media(type="film", title="Job orphelin", year=2020)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    dl = Download(media_item_id=item.id, state="pending", poll_quality_profile_id=999_999)
    db.add(dl)
    db.commit()

    resolution = resolve_download_poll_profile(db, media, settings, dl)
    assert resolution.missing_explicit
    assert resolution.profile is None
    from mediatracker.core.pipeline.constants import french_error
    from mediatracker.core.pipeline.download_pass_errors import compose_download_pass_failure
    from mediatracker.core.pipeline.jobs import _fail

    outcome = unlock_mod._from_hosters(
        db,
        dl,
        item,
        media,
        NOW,
        ["rapidgator"],
        None,
        MagicMock(),
        settings,
        [],
        defer_final=True,
    )
    assert outcome is None
    assert dl.error_code is None
    composed = compose_download_pass_failure(dl, ["wawacity"])
    assert composed is not None
    code, message = composed
    assert code == POLL_PROFILE_MISSING
    assert "introuvable" in message.lower()
    _fail(db, dl, code, french_error(code, message), NOW)
    db.refresh(dl)
    assert dl.error_code == POLL_PROFILE_MISSING


def test_poll_p2_apply_queues_download_unlock_consumes_profile_id(client, app, monkeypatch):
    """Sondage P2 → _apply crée Download.poll_quality_profile_id → unlock utilise ce lien."""
    from sqlalchemy import select

    from mediatracker.core.pipeline import unlock as unlock_mod
    from mediatracker.core.profile_attempt_orchestration import ProfileChain
    from mediatracker.core.quality_profile import profile_from_row
    from mediatracker.core.source_poll import PollReport
    from mediatracker.core.source_poll_phased import worker_remote_poll

    login(client)
    db = app.state.session_factory()
    try:
        p1_row = QualityProfile(
            name="Orchestré P1 VFF",
            languages=["VFF"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        p2_row = QualityProfile(
            name="Orchestré P2 VOSTFR",
            languages=["VOSTFR"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        db.add(p1_row)
        db.add(p2_row)
        media = Media(
            type="series",
            title="Poll P2 file",
            year=2024,
            followed=True,
            allow_fallback=True,
            new_episode_policy="auto",
        )
        db.add(media)
        db.flush()
        db.add(MediaItem(media_id=media.id, season=1, episode=1, state="missing", air_date=NOW.date()))
        commit_retry(db)
        media_id = media.id
        repli_id = p2_row.id
        chain = ProfileChain((profile_from_row(p1_row), profile_from_row(p2_row)))
        from mediatracker.db.models import Provider

        provider_id = db.scalar(select(Provider.id).limit(1))
    finally:
        db.close()

    class _P2LinkSource:
        capabilities = frozenset({"links"})
        type = "mock-p2"

        def search(self, *_a, **_k) -> list:
            return [
                SourceResult(
                    title="Poll P2 file",
                    year=2024,
                    ref="vost",
                    url="/vost",
                    quality="HD",
                    language="VOSTFR",
                    season=1,
                    episode=1,
                )
            ]

        def get_links(self, ref) -> list:
            return [
                LinkCandidate(
                    url="https://exemple.test/fichier-p2",
                    kind="ddl_hoster",
                    hoster="rapidgator",
                    quality="HD",
                    language="VOSTFR",
                    season=1,
                    episode=1,
                    external_id="p2-link",
                )
            ]

        def close(self) -> None:
            return None

    source = _P2LinkSource()
    real_worker = worker_remote_poll

    def gated_worker(factory, settings, media_id, provider_id, src, moment, draw, **kw):
        single = kw.get("single_profile")
        if single is not None and single.name == "Orchestré P1 VFF":
            return PollReport(
                result="nothing",
                message="P1 vide",
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=moment,
                next_check_at=moment,
            )
        return real_worker(factory, settings, media_id, provider_id, src, moment, draw, **kw)

    def open_remote(_db, _settings, type_name, **_k):
        if type_name == "wawacity":
            return provider_id, source
        return None

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: chain,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity",),
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", gated_worker)
    monkeypatch.setattr("mediatracker.core.source_binding.pick_bound_pages", lambda *_a, **_k: None)
    monkeypatch.setattr(unlock_mod, "_hoster_supported", lambda *_a, **_k: True)
    monkeypatch.setattr(unlock_mod, "_unlock", lambda *_a, **_k: ("http://u", "f", 1, "http://u"))
    monkeypatch.setattr(unlock_mod, "_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(unlock_mod, "_probed_direct", lambda *_a, **_k: None)

    report = orchestrate_profile_first_poll(
        app.state.session_factory,
        app.state.settings,
        media_id,
        force=True,
        now=NOW,
    )
    assert report is not None
    assert report.queued >= 1

    db2 = app.state.session_factory()
    try:
        renamed = db2.get(QualityProfile, repli_id)
        assert renamed is not None
        renamed.name = "Profil P2 renommé après plan"
        db2.commit()
        item_id = db2.scalar(select(MediaItem.id).where(MediaItem.media_id == media_id, MediaItem.season == 1))
        dl = db2.scalar(select(Download).where(Download.media_item_id == item_id))
        assert dl is not None
        assert dl.poll_quality_profile_id == repli_id
        item = db2.get(MediaItem, dl.media_item_id)
        media_row = db2.get(Media, media_id)
        unlock_mod._from_hosters(
            db2,
            dl,
            item,
            media_row,
            NOW,
            ["rapidgator"],
            None,
            MagicMock(),
            app.state.settings,
            [],
            defer_final=True,
        )
        assert dl.link_id is not None
    finally:
        db2.close()


def test_api_delete_refuses_profile_referenced_by_poll_download(client, db):
    login(client)
    from tests.conftest import csrf

    row = QualityProfile(
        name="Profil file protégé",
        languages=["VOSTFR"],
        min_resolution=None,
        max_resolution="1080p",
        is_default=False,
    )
    db.add(row)
    media = Media(type="film", title="Delete guard", year=2020)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    db.add(Download(media_item_id=item.id, state="pending", poll_quality_profile_id=row.id))
    db.commit()
    refused = client.delete(f"/api/v1/quality-profiles/{row.id}", headers=csrf(client))
    assert refused.status_code == 422
    assert "téléchargement" in refused.json()["detail"].lower() or "sondage" in refused.json()["detail"].lower()
    fresh = client.app.state.session_factory()
    try:
        still = fresh.get(QualityProfile, row.id)
        dl = fresh.scalar(select(Download).where(Download.media_item_id == item.id))
        assert still is not None
        assert dl is not None
        assert dl.poll_quality_profile_id == row.id
    finally:
        fresh.close()


def test_unlock_reingest_reloads_links_before_rechoose(monkeypatch):
    """Après réingest, la boucle doit relire les liens (pas la liste figée d'avant)."""
    from mediatracker.core.pipeline import unlock as unlock_mod

    scalar_calls: list[int] = []

    class _Q:
        def where(self, *_a, **_k):
            return self

        def all(self):
            scalar_calls.append(len(scalar_calls))
            link = MagicMock(id=10 + len(scalar_calls), dead=False, hoster="rapidgator", kind="http")
            return [link]

    db = MagicMock()
    db.scalars.return_value = _Q()
    item = MagicMock(id=1, season=1, episode=1)
    media = MagicMock(id=2, allow_fallback=True)
    row = MagicMock(
        skipped_link_ids=[],
        attempts=0,
        link_id=None,
        media_item_id=1,
        poll_quality_profile_id=None,
    )
    choose_calls = {"n": 0}

    def fake_choose(*_a, **_k):
        choose_calls["n"] += 1
        return MagicMock(id=42) if choose_calls["n"] >= 2 else None

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: ProfileChain((_principal_profile(),)),
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_adapters.choose_link_for_profile_attempt",
        fake_choose,
    )
    monkeypatch.setattr(unlock_mod, "_fallback_allowed", lambda *_a, **_k: True)
    monkeypatch.setattr(unlock_mod, "_ingest_other_versions", lambda *_a, **_k: True)
    monkeypatch.setattr(unlock_mod, "_hoster_supported", lambda *_a, **_k: True)
    monkeypatch.setattr(unlock_mod, "_unlock", lambda *_a, **_k: ("http://u", "f", 1, "http://u"))
    monkeypatch.setattr(unlock_mod, "_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(unlock_mod, "_probed_direct", lambda *_a, **_k: None)
    unlock_mod._from_hosters(
        db,
        row,
        item,
        media,
        NOW,
        ["rapidgator"],
        None,
        MagicMock(),
        MagicMock(),
        [],
        defer_final=True,
    )
    assert len(scalar_calls) >= 2
    assert choose_calls["n"] >= 2


def test_choose_link_returns_none_when_all_live_links_fail_profile(db, settings):
    from mediatracker.core.pipeline.jobs import _choose_link
    from mediatracker.core.quality_profile import profile_from_row
    from mediatracker.db.models import QualityProfile

    vost = QualityProfile(
        name="Profil VOSTFR seul",
        languages=["VOSTFR"],
        min_resolution=None,
        max_resolution="1080p",
        is_default=False,
    )
    db.add(vost)
    db.flush()
    spec = profile_from_row(vost)
    media = Media(type="film", title="Liens hors profil", year=2020, pref_quality="720p", pref_language="VFF")
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    provider_id = db.scalar(select(Provider.id).limit(1))
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://exemple.test/vff",
            hoster="rapidgator",
            quality="720p",
            language="VFF",
            external_id="only-vff",
            priority=1,
        )
    )
    dl = Download(media_item_id=item.id, state="pending", poll_quality_profile_id=vost.id)
    db.add(dl)
    db.commit()
    chosen = _choose_link(db, dl, item, ["rapidgator"], media=media, settings=settings, profile=spec)
    assert chosen is None


def test_rediscover_legacy_p2_unlock_same_session_without_caller_refresh(client, app, monkeypatch):
    """Rediscovery job NULL → P2 sur E1 → unlock voit poll_quality_profile_id (expire_on_commit=False)."""
    from mediatracker.core.pipeline import jobs as jobs_mod
    from mediatracker.core.pipeline import unlock as unlock_mod
    from mediatracker.core.pipeline.constants import REDISCOVER_RESERVED
    from mediatracker.core.profile_attempt_orchestration import ProfileChain
    from mediatracker.core.quality_profile import profile_from_row

    login(client)
    db = app.state.session_factory()
    try:
        p1_row = QualityProfile(
            name="Redisc P1 VFF",
            languages=["VFF"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        p2_row = QualityProfile(
            name="Redisc P2 VOSTFR",
            languages=["VOSTFR"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        db.add(p1_row)
        db.add(p2_row)
        media = Media(
            type="series",
            title="Rediscover intégré",
            year=2024,
            followed=True,
            allow_fallback=True,
            new_episode_policy="auto",
        )
        db.add(media)
        db.flush()
        item_e1 = MediaItem(media_id=media.id, season=1, episode=1, state="missing", air_date=NOW.date())
        db.add(item_e1)
        db.add(
            MediaItem(media_id=media.id, season=1, episode=2, state="missing", air_date=NOW.date()),
        )
        db.flush()
        dl = Download(
            media_item_id=item_e1.id,
            state="pending",
            error_code=REDISCOVER_RESERVED,
            poll_quality_profile_id=None,
        )
        db.add(dl)
        commit_retry(db)
        media_id = media.id
        p2_id = p2_row.id
        chain = ProfileChain((profile_from_row(p1_row), profile_from_row(p2_row)))
        provider_id = db.scalar(select(Provider.id).limit(1))
    finally:
        db.close()

    class _ScopedSource:
        capabilities = frozenset({"links"})
        type = "mock-scoped"

        def search(self, *_a, **_k) -> list:
            return [
                SourceResult(
                    title="Rediscover intégré",
                    year=2024,
                    ref="ep1-vost",
                    url="/ep1",
                    quality="HD",
                    language="VOSTFR",
                    season=1,
                    episode=1,
                ),
                SourceResult(
                    title="Rediscover intégré",
                    year=2024,
                    ref="ep2-vff",
                    url="/ep2",
                    quality="HD",
                    language="VFF",
                    season=1,
                    episode=2,
                ),
            ]

        def get_links(self, ref) -> list:
            if ref.ref == "ep1-vost":
                return [
                    LinkCandidate(
                        url="https://exemple.test/e1-vost",
                        kind="ddl_hoster",
                        hoster="rapidgator",
                        quality="HD",
                        language="VOSTFR",
                        season=1,
                        episode=1,
                        external_id="e1-vost",
                    )
                ]
            return [
                LinkCandidate(
                    url="https://exemple.test/e2-vff",
                    kind="ddl_hoster",
                    hoster="rapidgator",
                    quality="HD",
                    language="VFF",
                    season=1,
                    episode=2,
                    external_id="e2-vff",
                )
            ]

        def close(self) -> None:
            return None

    source = _ScopedSource()

    def open_remote(_db, _settings, type_name, **_k):
        if type_name == "wawacity":
            return provider_id, source
        return None

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: chain,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("wawacity",),
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.hive.identity.hive_enabled_in_db", lambda _db: False)
    monkeypatch.setattr("mediatracker.core.source_binding.pick_bound_pages", lambda *_a, **_k: None)
    monkeypatch.setattr(unlock_mod, "_hoster_supported", lambda *_a, **_k: True)
    monkeypatch.setattr(unlock_mod, "_unlock", lambda *_a, **_k: ("http://u", "f", 1, "http://u"))
    monkeypatch.setattr(unlock_mod, "_commit", lambda *_a, **_k: None)
    monkeypatch.setattr(unlock_mod, "_probed_direct", lambda *_a, **_k: None)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    db2 = app.state.session_factory()
    try:
        media = db2.get(Media, media_id)
        dl_row = db2.scalar(
            select(Download).where(Download.media_item_id == item_e1.id)
        )
        assert media is not None and dl_row is not None
        media.last_poll_message = "écriture antérieure tick"
        db2.flush()
        jobs_mod._rediscover(
            db2,
            app.state.settings,
            media,
            NOW,
            download=dl_row,
            search_slot_reserved=True,
        )
        assert dl_row.poll_quality_profile_id == p2_id
        item = db2.get(MediaItem, dl_row.media_item_id)
        unlock_mod._from_hosters(
            db2,
            dl_row,
            item,
            media,
            NOW,
            ["rapidgator"],
            None,
            MagicMock(),
            app.state.settings,
            [],
            defer_final=True,
        )
        assert dl_row.link_id is not None
    finally:
        db2.close()


def test_rediscover_hive_p1_stops_without_remote_when_no_hoster_link(client, app, monkeypatch):
    """Hive P1 file sur E1 sans lien hoster : pas de tentative P2 distante, SHA E1 figé."""
    from tests.test_hive_profile_attempt import _seed_catalogue_row
    from tests.test_hive_t6 import _enable, _ruche

    from mediatracker.core.pipeline import jobs as jobs_mod
    from mediatracker.core.pipeline.constants import REDISCOVER_RESERVED
    from mediatracker.core.profile_attempt_orchestration import (
        ProfileChain,
        active_hive_fetch_sha_for_item,
    )
    from mediatracker.core.quality_profile import profile_from_row
    from mediatracker.core.source_poll import PollReport

    _enable(client)
    login(client)
    ruche_hex = _ruche(client).hex()
    db = app.state.session_factory()
    sha_e1 = "1" * 64
    sha_e2 = "2" * 64
    try:
        p1_row = QualityProfile(
            name="Hive redisc P1 VFF",
            languages=["VFF"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        p2_row = QualityProfile(
            name="Hive redisc P2 VOSTFR",
            languages=["VOSTFR"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        db.add(p1_row)
        db.add(p2_row)
        media = Media(
            type="series",
            title="Hive rediscover intégré",
            year=2099,
            tmdb_id=8810,
            followed=True,
            allow_fallback=True,
            new_episode_policy="auto",
            pref_quality="HD",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        item_e1 = MediaItem(media_id=media.id, season=1, episode=1, state="missing", air_date=NOW.date())
        item_e2 = MediaItem(media_id=media.id, season=1, episode=2, state="missing", air_date=NOW.date())
        db.add_all([item_e1, item_e2])
        db.flush()
        dl = Download(
            media_item_id=item_e1.id,
            state="pending",
            error_code=REDISCOVER_RESERVED,
            poll_quality_profile_id=None,
        )
        db.add(dl)
        commit_retry(db)
        media_id = media.id
        dl_id = dl.id
        p1_id = p1_row.id
        item_e1_id = item_e1.id
        chain = ProfileChain((profile_from_row(p1_row), profile_from_row(p2_row)))
        _seed_catalogue_row(
            db,
            cle={"type": "series", "tmdb_id": 8810, "saison": 1, "episode": 1, "version": "HD+VFF"},
            version="HD+VFF",
            sha=sha_e1,
            joignable=True,
            ruche_hex=ruche_hex,
        )
        _seed_catalogue_row(
            db,
            cle={"type": "series", "tmdb_id": 8810, "saison": 1, "episode": 2, "version": "1080p+VOSTFR"},
            version="1080p+VOSTFR",
            sha=sha_e2,
            joignable=True,
            ruche_hex=ruche_hex,
        )
        from tests.test_hive_catalogue_request import _ensure_hive_enqueue_prereqs

        _ensure_hive_enqueue_prereqs(db, ruche_hex)
        commit_retry(db)
    finally:
        db.close()

    remote_calls: list[str] = []

    class _WouldWinRemote:
        capabilities = frozenset({"links"})
        type = "mock-p2-remote"

        def search(self, *_a, **_k):
            return [
                SourceResult(
                    title="Hive rediscover intégré",
                    year=2099,
                    ref="ep1",
                    url="/ep1",
                    quality="HD",
                    language="VOSTFR",
                    season=1,
                    episode=1,
                )
            ]

        def get_links(self, ref):
            return [
                LinkCandidate(
                    url="https://exemple.test/e1-vost-remote",
                    kind="ddl_hoster",
                    hoster="rapidgator",
                    quality="HD",
                    language="VOSTFR",
                    season=1,
                    episode=1,
                    external_id="remote-p2",
                )
            ]

        def close(self):
            return None

    source = _WouldWinRemote()
    provider_id = app.state.session_factory().scalar(select(Provider.id).limit(1))

    def open_remote(_db, _settings, type_name, **_k):
        if type_name == "wawacity":
            return provider_id, source
        return None

    def guarded_worker(factory, settings, mid, prov, src, moment, draw, **kw):
        prof = kw.get("single_profile")
        remote_calls.append(prof.name if prof else "?")
        return PollReport(
            result="found",
            message="distant aurait gagné",
            episodes=1,
            queued=1,
            proposed=0,
            last_check_at=moment,
            next_check_at=moment,
        )

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: chain,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("hive", "wawacity"),
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", guarded_worker)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    db2 = app.state.session_factory()
    try:
        media = db2.get(Media, media_id)
        dl_row = db2.get(Download, dl_id)
        assert media is not None and dl_row is not None
        jobs_mod._rediscover(
            db2,
            app.state.settings,
            media,
            NOW,
            download=dl_row,
            search_slot_reserved=True,
        )
        item = db2.get(MediaItem, item_e1_id)
        assert item is not None
        assert remote_calls == []
        assert dl_row.poll_quality_profile_id == p1_id
        assert active_hive_fetch_sha_for_item(db2, media, item) == sha_e1
        ctx = db2.scalar(
            select(DownloadHiveFetch).where(DownloadHiveFetch.sha256_hex == sha_e1).limit(1)
        )
        assert ctx is not None
        assert db2.scalar(select(Link).where(Link.media_item_id == item_e1_id)) is None
    finally:
        db2.close()


def test_rediscover_hive_gate_accepts_queued_sha_when_better_rank_unavailable(client, app, monkeypatch):
    """Deux versions E1 : VFF indispo mieux classée, VOSTFR dispo file → gate valide le SHA B exact."""
    from tests.test_hive_profile_attempt import _seed_catalogue_row
    from tests.test_hive_t6 import _enable, _ruche

    from mediatracker.core.pipeline import jobs as jobs_mod
    from mediatracker.core.pipeline.constants import REDISCOVER_RESERVED
    from mediatracker.core.profile_attempt_orchestration import (
        ProfileChain,
        active_hive_fetch_sha_for_item,
    )
    from mediatracker.core.quality_profile import profile_from_row
    from mediatracker.core.source_poll import PollReport

    _enable(client)
    login(client)
    ruche_hex = _ruche(client).hex()
    db = app.state.session_factory()
    sha_a = "a" * 64
    sha_b = "b" * 64
    try:
        hybrid = QualityProfile(
            name="Hive gate hybride",
            languages=["VFF", "VOSTFR"],
            min_resolution=None,
            max_resolution="1080p",
            is_default=False,
        )
        db.add(hybrid)
        media = Media(
            type="series",
            title="Hive gate rang dispo",
            year=2099,
            tmdb_id=8811,
            followed=True,
            allow_fallback=True,
            new_episode_policy="auto",
            pref_quality="HD",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        item_e1 = MediaItem(media_id=media.id, season=1, episode=1, state="missing", air_date=NOW.date())
        db.add(item_e1)
        db.flush()
        dl = Download(
            media_item_id=item_e1.id,
            state="pending",
            error_code=REDISCOVER_RESERVED,
            poll_quality_profile_id=None,
        )
        db.add(dl)
        commit_retry(db)
        media_id = media.id
        dl_id = dl.id
        hybrid_id = hybrid.id
        item_e1_id = item_e1.id
        chain = ProfileChain((profile_from_row(hybrid),))
        _seed_catalogue_row(
            db,
            cle={"type": "series", "tmdb_id": 8811, "saison": 1, "episode": 1, "version": "HD+VFF"},
            version="HD+VFF",
            sha=sha_a,
            joignable=False,
            ruche_hex=ruche_hex,
        )
        _seed_catalogue_row(
            db,
            cle={"type": "series", "tmdb_id": 8811, "saison": 1, "episode": 1, "version": "1080p+VOSTFR"},
            version="1080p+VOSTFR",
            sha=sha_b,
            joignable=True,
            ruche_hex=ruche_hex,
        )
        from tests.test_hive_catalogue_request import _ensure_hive_enqueue_prereqs

        _ensure_hive_enqueue_prereqs(db, ruche_hex)
        commit_retry(db)
    finally:
        db.close()

    remote_calls: list[str] = []

    class _RemoteGuard:
        capabilities = frozenset({"links"})
        type = "mock-remote-guard"

        def search(self, *_a, **_k):
            return []

        def get_links(self, ref):
            return []

        def close(self):
            return None

    provider_id = app.state.session_factory().scalar(select(Provider.id).limit(1))

    def open_remote(_db, _settings, type_name, **_k):
        if type_name == "wawacity":
            return provider_id, _RemoteGuard()
        return None

    def guarded_worker(factory, settings, mid, prov, src, moment, draw, **kw):
        prof = kw.get("single_profile")
        remote_calls.append(prof.name if prof else "?")
        return PollReport(
            result="found",
            message="distant",
            episodes=1,
            queued=1,
            proposed=0,
            last_check_at=moment,
            next_check_at=moment,
        )

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.load_profile_chain",
        lambda *_a, **_k: chain,
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("hive", "wawacity"),
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", guarded_worker)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    db2 = app.state.session_factory()
    try:
        media = db2.get(Media, media_id)
        dl_row = db2.get(Download, dl_id)
        assert media is not None and dl_row is not None
        jobs_mod._rediscover(
            db2,
            app.state.settings,
            media,
            NOW,
            download=dl_row,
            search_slot_reserved=True,
        )
        item = db2.get(MediaItem, item_e1_id)
        assert item is not None
        assert remote_calls == []
        assert dl_row.poll_quality_profile_id == hybrid_id
        assert active_hive_fetch_sha_for_item(db2, media, item) == sha_b
        assert db2.scalar(select(DownloadHiveFetch).where(DownloadHiveFetch.sha256_hex == sha_a)) is None
        assert db2.scalar(select(DownloadHiveFetch).where(DownloadHiveFetch.sha256_hex == sha_b)) is not None
    finally:
        db2.close()


def test_hive_unreachable_peer_falls_back_to_remote_source(client, app, monkeypatch):
    """Catalogue joignable mais pair sans adresse publique : repli vers la source suivante (voulu)."""
    from tests.test_hive_profile_attempt import _seed_catalogue_row
    from tests.test_hive_t6 import _enable, _ruche

    from mediatracker.core.quality_profile import ensure_default_profile
    from mediatracker.core.source_poll import PollReport
    from mediatracker.db.models import Setting
    from mediatracker.hive.identity import HIVE_ENABLED_KEY

    _enable(client)
    login(client)
    ruche_hex = _ruche(client).hex()
    db = app.state.session_factory()
    try:
        ensure_default_profile(db)
        db.merge(Setting(key=HIVE_ENABLED_KEY, value="true", is_secret=False, updated_at=NOW))
        media = Media(
            type="series",
            title="Hive repli distant",
            year=2099,
            tmdb_id=8812,
            followed=True,
            allow_fallback=True,
            new_episode_policy="auto",
            pref_quality="HD",
            pref_language="VFF",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, state="missing", air_date=NOW.date())
        db.add(item)
        db.flush()
        _seed_catalogue_row(
            db,
            cle={"type": "series", "tmdb_id": 8812, "saison": 1, "episode": 1, "version": "HD+VFF"},
            version="HD+VFF",
            sha="f" * 64,
            joignable=True,
            ruche_hex=ruche_hex,
        )
        commit_retry(db)
        media_id = media.id
    finally:
        db.close()

    remote_calls: list[str] = []

    class _RemoteStub:
        capabilities = frozenset({"links"})
        type = "mock-wawacity"

        def search(self, *_a, **_k):
            return []

        def get_links(self, *_a, **_k):
            return []

        def close(self):
            return None

    provider_id = app.state.session_factory().scalar(select(Provider.id).limit(1))

    def open_remote(_db, _settings, type_name, **_k):
        if type_name == "wawacity":
            return provider_id, _RemoteStub()
        return None

    def guarded_worker(factory, settings, mid, prov, src, moment, draw, **kw):
        remote_calls.append(kw.get("single_profile").name if kw.get("single_profile") else "?")
        return PollReport(
            result="found",
            message="distant",
            episodes=1,
            queued=1,
            proposed=0,
            last_check_at=moment,
            next_check_at=moment,
        )

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.download_source_order",
        lambda *_a, **_k: ("hive", "wawacity"),
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr("mediatracker.core.source_poll_phased.worker_remote_poll", guarded_worker)
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)

    factory = app.state.session_factory
    orchestrate_profile_first_poll(factory, app.state.settings, media_id, force=True, now=NOW)
    assert len(remote_calls) >= 1
    db2 = app.state.session_factory()
    try:
        assert db2.scalar(select(DownloadHiveFetch).limit(1)) is None
    finally:
        db2.close()


def test_rediscover_missing_explicit_skips_orchestrator(db, settings, monkeypatch):
    from mediatracker.core.pipeline import jobs as jobs_mod
    from mediatracker.core.profile_attempt_orchestration import POLL_PROFILE_MISSING

    calls: list[int] = []

    def fake_orch(*_a, **_k):
        calls.append(1)
        return None

    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.orchestrate_profile_first_poll",
        fake_orch,
    )
    media = Media(type="film", title="Explicit manquant", year=2020, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    dl = Download(media_item_id=item.id, state="pending", poll_quality_profile_id=999_999)
    db.add(dl)
    db.commit()
    jobs_mod._rediscover(db, settings, media, NOW, download=dl)
    assert calls == []
    assert dl.error_code == POLL_PROFILE_MISSING


def test_choose_link_uses_poll_profile_not_pref_csv(db, settings):
    from mediatracker.core.pipeline.jobs import _choose_link
    from mediatracker.core.quality_profile import profile_from_row
    row = QualityProfile(
        name="Profil file VOSTFR",
        languages=["VOSTFR"],
        min_resolution=None,
        max_resolution="1080p",
        is_default=False,
    )
    db.add(row)
    media = Media(type="film", title="Choix file", year=2020, pref_quality="720p", pref_language="VFF")
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, state="missing")
    db.add(item)
    db.flush()
    provider_id = db.scalar(select(Provider.id).limit(1))
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://exemple.test/vost",
            hoster="rapidgator",
            quality="HD",
            language="VOSTFR",
            external_id="vost",
            priority=1,
        )
    )
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://exemple.test/vff",
            hoster="rapidgator",
            quality="720p",
            language="VFF",
            external_id="vff",
            priority=10,
        )
    )
    dl = Download(media_item_id=item.id, state="pending", poll_quality_profile_id=row.id)
    db.add(dl)
    db.commit()
    profile = profile_from_row(row)
    chosen = _choose_link(
        db,
        dl,
        item,
        ["rapidgator"],
        media=media,
        settings=settings,
        profile=profile,
    )
    assert chosen is not None
    assert chosen.language == "VOSTFR"


def test_ingest_other_versions_tries_second_source_in_user_order(client, app, monkeypatch):
    from mediatracker.core.pipeline.jobs import _ingest_other_versions
    from mediatracker.core.quality_profile import ensure_default_profile, profile_from_row
    db = app.state.session_factory()
    try:
        login(client)
        ensure_default_profile(db)
        qp = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)))
        assert qp is not None
        spec = profile_from_row(qp)
        media = Media(
            type="series",
            title="Ingest multi",
            year=2022,
            followed=True,
            source_order="mock_a,mock_b",
        )
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
        db.add(item)
        commit_retry(db)
        media_id = media.id
        item_id = item.id
    finally:
        db.close()

    tried: list[str] = []

    class _Base:
        capabilities = frozenset({"links"})

        def close(self) -> None:
            return None

    class _EmptySource(_Base):
        def search(self, *_a, **_k):
            return []

        def get_links(self, *_a, **_k):
            return []

    class _HitSource(_Base):
        def search(self, *_a, **_k):
            return [
                SourceResult(
                    title="Ingest multi",
                    year=2022,
                    ref="hit",
                    url="/hit",
                    quality="HD",
                    language="VFF",
                    season=1,
                    episode=1,
                )
            ]

        def get_links(self, ref) -> list:
            return [
                LinkCandidate(
                    url="https://exemple.test/nouveau",
                    kind="ddl_hoster",
                    hoster="rapidgator",
                    quality="HD",
                    language="VFF",
                    season=1,
                    episode=1,
                )
            ]

    def open_remote(_db, _settings, type_name, **_k):
        tried.append(type_name)
        if type_name == "mock_a":
            return 1, _EmptySource()
        if type_name == "mock_b":
            return 2, _HitSource()
        return None

    def fake_for_type(name):
        if name in {"mock_a", "mock_b"}:
            return type("Entry", (), {"capabilities": frozenset({"links"})})
        return None

    monkeypatch.setattr("mediatracker.providers.sources.registry.for_type", fake_for_type)
    monkeypatch.setattr(
        "mediatracker.core.pipeline.jobs._usable_download_sources",
        lambda *_a, **_k: ["mock_a", "mock_b"],
    )
    monkeypatch.setattr("mediatracker.providers.sources.loading.remote_links_source", open_remote)
    monkeypatch.setattr(
        "mediatracker.core.source_poll._search",
        lambda source, *_a, **_k: source.search(),
    )
    monkeypatch.setattr("mediatracker.core.remote_search_limit.begin_remote_search", lambda *_a, **_k: True)
    monkeypatch.setattr("mediatracker.core.pipeline.jobs.hoster_allowed", lambda *_a, **_k: True)

    def fake_store(db, item, kept, provider_id, preference, now):
        if not kept:
            return
        db.add(
            Link(
                media_item_id=item.id,
                provider_id=provider_id,
                kind="ddl_hoster",
                url=kept[0].url,
                hoster=kept[0].hoster or "rapidgator",
                quality=kept[0].quality or "HD",
                language=kept[0].language or "VFF",
                external_id="ingest-new",
                priority=1,
            )
        )

    monkeypatch.setattr("mediatracker.core.source_poll._store_links", fake_store)

    db2 = app.state.session_factory()
    try:
        media = db2.get(Media, media_id)
        item = db2.get(MediaItem, item_id)
        assert media is not None and item is not None
        _ingest_other_versions(db2, app.state.settings, media, item, NOW, attempt_profile=spec)
        db2.commit()
        assert tried == ["mock_a", "mock_b"], f"tried={tried!r}"
    finally:
        db2.close()

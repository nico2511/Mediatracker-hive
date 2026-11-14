"""Sondage source : SQLite, SAVEPOINT worker, concurrence (Suivre / worker)."""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_wawacity import BASE, NOW, _episodes, _pages, _poll, _series, _source

from mediatracker.core.source_poll import run_poll
from mediatracker.core.source_poll_tick import prepare_due_media, worker_poll_if_due
from mediatracker.db import session as session_mod
from mediatracker.db.models import Download, Event, Media, MediaItem, Provider, SourceBinding
from mediatracker.db.session import commit_retry, release_sqlite_before_network
from mediatracker.providers.base import LinkCandidate, SourceFetchError, SourceResult
from mediatracker.providers.sources.wawacity import HtmlCache

FAST_WRITE_MAX_S = 0.35
NETWORK_HOLD_S = 0.55


def _slow_handler(
    calls: list[str],
    network_gate: threading.Event,
    delay: float = NETWORK_HOLD_S,
    *,
    first_request_only: bool = False,
):
    inner = _pages(calls)
    armed = [True]

    def handler(request: httpx.Request) -> httpx.Response:
        if not first_request_only or armed[0]:
            if first_request_only:
                armed[0] = False
            network_gate.set()
            time.sleep(delay)
        return inner(request)

    return handler


def _seed_obtained_film_plus_due_series(client, app) -> tuple[int, int]:
    """Un film obtenu (retiré par next_due) puis une série due pour le sondage."""
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "TestAgent/1.0"}},
        headers=csrf(client),
    )
    series_json = _series(client, policy="auto")
    _episodes(client, series_json["id"])
    db = app.state.session_factory()
    try:
        film = Media(
            type="film",
            title="Film-obtenu-poll",
            year=2019,
            followed=True,
            new_episode_policy="auto",
            next_source_poll_at=NOW.replace(tzinfo=None),
            created_at=NOW,
        )
        db.add(film)
        db.flush()
        db.add(
            MediaItem(
                media_id=film.id,
                title=film.title,
                state="obtained",
                downloaded=True,
            )
        )
        series = db.get(Media, series_json["id"])
        assert series is not None
        series.next_source_poll_at = NOW.replace(tzinfo=None)
        db.commit()
        return film.id, series.id
    finally:
        db.close()


class _GateSource:
    """Source minimale : bloque sur search jusqu'au signal d'écriture concurrente."""

    capabilities = frozenset({"links"})
    type = "gate-mock"

    def __init__(
        self,
        network_gate: threading.Event,
        write_done: threading.Event,
        *,
        timeout: float = 3.0,
    ) -> None:
        self._network_gate = network_gate
        self._write_done = write_done
        self._timeout = timeout

    def search(self, *_args, **_kwargs) -> list:
        self._network_gate.set()
        assert self._write_done.wait(timeout=self._timeout), "écriture concurrente absente"
        return []

    def get_links(self, *_args, **_kwargs) -> list:
        return []

    def close(self) -> None:
        return None


def _run_worker_poll_if_due(app, start_gate: threading.Event, errors: list[str], source) -> None:
    """Vraie entrée production worker_poll_if_due (préparation commitée + apply)."""
    factory = app.state.session_factory
    settings = app.state.settings
    prep = factory()
    try:
        provider_id = prep.query(Provider).filter(Provider.type == "wawacity").one().id
    finally:
        prep.close()
    try:
        start_gate.wait(timeout=5)
        worker_poll_if_due(
            factory,
            settings,
            now=NOW,
            rng=random.Random(5),
            sources=[(provider_id, source)],
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        source.close()


def _prepare_followed_series(client, app) -> int:
    login(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"wawacity_base_url": BASE, "wawacity_user_agent": "TestAgent/1.0"}},
        headers=csrf(client),
    )
    media = _series(client, policy="auto")
    _episodes(client, media["id"])
    db = app.state.session_factory()
    try:
        row = db.get(Media, media["id"])
        assert row is not None
        row.next_source_poll_at = NOW.replace(tzinfo=None)
        db.commit()
    finally:
        db.close()
    return media["id"]


def _write_during_network(
    factory,
    network_gate: threading.Event,
    title: str,
    *,
    write_done: threading.Event | None = None,
) -> float:
    assert network_gate.wait(timeout=3), "le sondage n'est pas entré en phase réseau"
    session = factory()
    try:
        started = time.monotonic()
        session.add(
            Media(
                type="film",
                title=title,
                year=2020,
                followed=False,
                created_at=datetime.now(timezone.utc),
            )
        )
        commit_retry(session)
        return time.monotonic() - started
    finally:
        session.close()
        if write_done is not None:
            write_done.set()


def _run_poll_in_session(
    app,
    media_id: int,
    start_gate: threading.Event,
    network_gate: threading.Event,
    errors: list[str],
    *,
    handler,
    use_savepoint: bool,
    flush_before_network: bool,
    commit: bool,
) -> None:
    session = app.state.session_factory()
    try:
        start_gate.wait(timeout=5)
        media = session.get(Media, media_id)
        assert media is not None
        media.last_poll_message = "marqueur-avant-reseau"
        if flush_before_network:
            session.flush()
        provider = session.query(Provider).filter(Provider.type == "wawacity").one()
        source = _source(handler, cache=HtmlCache(0))

        def _body() -> None:
            run_poll(
                session,
                media,
                provider.id,
                source,
                app.state.settings,
                now=NOW,
                rng=random.Random(3),
                force=True,
                commit=commit,
            )

        try:
            if use_savepoint:
                with session.begin_nested():
                    _body()
            else:
                _body()
            session.commit()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            source.close()
    finally:
        session.close()


def test_release_sqlite_never_commits_inside_savepoint(app, monkeypatch):
    """P1 : le helper ne doit pas appeler commit sous begin_nested (régression prouvée)."""
    commits: list[str] = []
    real = session_mod.commit_retry

    def spy(db, *, attempts: int = 3):
        commits.append("commit_retry")
        return real(db, attempts=attempts)

    monkeypatch.setattr(session_mod, "commit_retry", spy)
    session = app.state.session_factory()
    try:
        session.execute(select(1))
        with session.begin_nested():
            release_sqlite_before_network(session)
            session.execute(select(1))
        assert commits == [], commits
    finally:
        session.close()


def test_commit_inside_savepoint_closes_transaction():
    """Preuve SQLAlchemy : commit dans begin_nested casse la session."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    eng = create_engine("sqlite:////tmp/mt_nested_probe.db")
    Session = sessionmaker(bind=eng)
    db = Session()
    db.execute(text("CREATE TABLE IF NOT EXISTS probe (id INTEGER)"))
    db.commit()
    db = Session()
    with pytest.raises(Exception) as raised:
        with db.begin_nested():
            db.execute(text("INSERT INTO probe VALUES (1)"))
            db.commit()
            db.execute(text("SELECT 1"))
    assert "closed transaction" in str(raised.value).lower()
    db.close()


def test_worker_savepoint_poll_completes_after_slow_network(client, app):
    """Parcours worker réel : SAVEPOINT + commit=False + SQL après réseau."""
    media_id = _prepare_followed_series(client, app)
    errors: list[str] = []
    start_gate = threading.Event()
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate)
    start_gate.set()
    _run_poll_in_session(
        app,
        media_id,
        start_gate,
        network_gate,
        errors,
        handler=handler,
        use_savepoint=True,
        flush_before_network=False,
        commit=False,
    )
    assert not errors, errors
    assert network_gate.is_set()
    db = app.state.session_factory()
    try:
        row = db.get(Media, media_id)
        assert row is not None
        assert row.last_poll_result == "found"
    finally:
        db.close()


def test_api_check_poll_releases_lock_with_commit(client, app):
    """POST /check (commit=True, hors SAVEPOINT) : commit avant réseau."""
    media_id = _prepare_followed_series(client, app)
    errors: list[str] = []
    start_gate = threading.Event()
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate)
    worker = threading.Thread(
        target=_run_poll_in_session,
        args=(app, media_id, start_gate, network_gate, errors),
        kwargs={
            "handler": handler,
            "use_savepoint": False,
            "flush_before_network": True,
            "commit": True,
        },
    )
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "Film api ok")
    worker.join(timeout=60)
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, f"write waited {elapsed:.2f}s"


def test_worker_poll_if_due_retire_then_poll_persists_deferred_event(client, app):
    """Retrait film obtenu via next_due puis sondage : journal unfollow après réseau."""
    _film_id, series_id = _seed_obtained_film_plus_due_series(client, app)
    errors: list[str] = []
    start_gate = threading.Event()
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate, first_request_only=True)
    source = _source(handler, cache=HtmlCache(0))
    start_gate.set()
    _run_worker_poll_if_due(app, start_gate, errors, source)
    assert not errors, errors
    assert network_gate.is_set()
    db = app.state.session_factory()
    try:
        unfollowed = db.scalar(
            select(Event).where(Event.type == "media.unfollowed").order_by(Event.id.desc())
        )
        assert unfollowed is not None
        assert db.get(Media, unfollowed.media_id).followed is False
        assert db.get(Media, series_id).last_poll_result == "found"
    finally:
        db.close()


def test_worker_poll_if_due_concurrent_write_during_network(app):
    """Écrivain concurrent pendant la première requête lente (après retrait film obtenu)."""
    with TestClient(app) as client:
        _seed_obtained_film_plus_due_series(client, app)
    # Fermer le client HTTP avant le thread : sinon il garde une connexion SQLite.
    errors: list[str] = []
    start_gate = threading.Event()
    network_gate = threading.Event()
    write_done = threading.Event()
    source = _GateSource(network_gate, write_done)
    worker = threading.Thread(
        target=_run_worker_poll_if_due,
        args=(app, start_gate, errors, source),
    )
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(
        app.state.session_factory,
        network_gate,
        "Film apres retire",
        write_done=write_done,
    )
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, f"écriture bloquée {elapsed:.2f}s pendant la 1re requête lente"
    verify = app.state.session_factory()
    try:
        events = list(
            verify.scalars(select(Event).where(Event.type == "media.unfollowed")).all()
        )
        assert len(events) == 1
        retired = verify.get(Media, events[0].media_id)
        assert retired is not None and retired.followed is False
    finally:
        verify.close()


def test_worker_prepare_commits_unfollow_before_network(client, app):
    """Phase préparation : retrait + événement visibles en session neuve avant réseau."""
    film_id, _series_id = _seed_obtained_film_plus_due_series(client, app)
    assert prepare_due_media(app.state.session_factory, app.state.settings, now=NOW) == _series_id
    fresh = app.state.session_factory()
    try:
        assert (
            fresh.scalar(select(Event).where(Event.type == "media.unfollowed", Event.media_id == film_id))
            is not None
        )
        assert fresh.get(Media, film_id).followed is False
    finally:
        fresh.close()


def test_run_worker_pass_pre_poll_commit_survives_isolated_poll_failure(client, app, monkeypatch):
    """commit_retry avant sondage : une erreur isolée ne doit pas annuler l'étape précédente."""
    import mediatracker.worker as worker
    db = app.state.session_factory()
    factory = app.state.session_factory
    settings = app.state.settings

    def stamp_tick(session, _settings, commit=False):
        del commit
        session.add(
            Media(
                type="film",
                title="pre-poll-worker-marker",
                year=2022,
                followed=False,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.flush()

    monkeypatch.setattr(worker, "tick", stamp_tick)
    def _fail_poll(*_a, **_k):
        raise RuntimeError("sondage isolé simulé")

    monkeypatch.setattr(worker, "pump_forced_source_check_worker", lambda *_a, **_k: False)
    monkeypatch.setattr(worker, "worker_poll_if_due", _fail_poll)

    worker._run_worker_pass(db, settings, factory)
    db.close()
    fresh = factory()
    try:
        row = fresh.scalar(select(Media).where(Media.title == "pre-poll-worker-marker"))
        assert row is not None
    finally:
        fresh.close()


def test_apply_retry_reuses_collected_network_payload(client, app, monkeypatch):
    """Le retry apply_with_retry ne doit pas rejouer search/get_links."""
    from sqlalchemy.exc import OperationalError

    from mediatracker.core.source_poll_phased import network_call_count, reset_network_call_count

    media_id = _prepare_followed_series(client, app)
    factory = app.state.session_factory
    settings = app.state.settings
    errors: list[str] = []
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate, delay=0.05)
    source = _source(handler, cache=HtmlCache(0))
    prep = factory()
    try:
        provider_id = prep.query(Provider).filter(Provider.type == "wawacity").one().id
    finally:
        prep.close()

    reset_network_call_count()
    commits = {"n": 0}
    real = session_mod.commit_retry

    def flaky_commit(db, *, attempts=3):
        commits["n"] += 1
        if commits["n"] == 3:
            raise OperationalError("database is locked", {}, None)
        return real(db, attempts=attempts)

    monkeypatch.setattr(session_mod, "commit_retry", flaky_commit)
    try:
        worker_poll_if_due(
            factory,
            settings,
            now=NOW,
            rng=random.Random(7),
            sources=[(provider_id, source)],
        )
    except Exception as exc:
        errors.append(str(exc))
    finally:
        source.close()
    assert not errors, errors
    assert network_gate.is_set()
    expected_http = network_call_count()
    assert expected_http == 2, f"attendu 1 search + 1 get_links, obtenu {expected_http}"
    assert commits["n"] >= 2, "apply doit avoir retenté le commit sans repasser par le réseau"
    assert network_call_count() == expected_http
    verify = factory()
    try:
        assert verify.get(Media, media_id).last_poll_result == "found"
    finally:
        verify.close()


class _SearchFailSource:
    capabilities = frozenset({"links"})
    type = "search-fail-mock"

    def search(self, *_args, **_kwargs) -> list:
        raise SourceFetchError("HTTP", "fournisseur indisponible", retryable=True)

    def get_links(self, *_args, **_kwargs) -> list:
        return []

    def close(self) -> None:
        return None


BOUND_REF = "bound-broken-page"
GOOD_REF = "replacement-page-ref"


class _BindingFallbackSource:
    capabilities = frozenset({"links"})
    type = "binding-fallback-mock"
    search_calls = 0
    get_links_calls = 0
    get_links_refs: list[str] = []

    def search(self, title: str, media_type: str) -> list:
        type(self).search_calls += 1
        return [
            SourceResult(
                title=title,
                year=2023,
                media_type="series",
                ref=GOOD_REF,
                url=f"{BASE}/serie/{GOOD_REF}",
                season=1,
                quality="1080p",
                language="MULTI",
            )
        ]

    def get_links(self, ref) -> list:
        type(self).get_links_calls += 1
        type(self).get_links_refs.append(ref.ref)
        if ref.ref == BOUND_REF:
            raise SourceFetchError("HTTP", "page liée introuvable", retryable=True)
        return [
            LinkCandidate(
                url="https://ddl.test/fichier",
                kind="ddl_hoster",
                hoster="1fichier",
                season=1,
                episode=1,
                external_id="ext-1",
            )
        ]

    def close(self) -> None:
        return None


def test_worker_search_fetch_error_persists_backoff(client, app):
    """SourceFetchError sur search : apply persiste error/backoff, pas d'exception échappée."""
    media_id = _prepare_followed_series(client, app)
    factory = app.state.session_factory
    prep = factory()
    try:
        provider_id = prep.query(Provider).filter(Provider.type == "wawacity").one().id
    finally:
        prep.close()
    source = _SearchFailSource()
    worker_poll_if_due(
        factory,
        app.state.settings,
        now=NOW,
        rng=random.Random(11),
        sources=[(provider_id, source)],
    )
    fresh = factory()
    try:
        media = fresh.get(Media, media_id)
        assert media is not None
        assert media.last_poll_result == "error"
        assert (media.source_error_streak or 0) >= 1
        assert media.next_source_poll_at is not None
        assert "indisponible" in (media.last_poll_message or "")
    finally:
        fresh.close()


def test_worker_binding_get_links_error_search_replacement(client, app, monkeypatch):
    """Binding cassé : marquage + recherche de remplacement dans la collecte, apply atomique."""
    media_id = _prepare_followed_series(client, app)
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        media = db.get(Media, media_id)
        assert media is not None
        media.next_source_poll_at = NOW.replace(tzinfo=None)
        db.add(
            SourceBinding(
                media_id=media_id,
                provider_id=provider.id,
                external_ref=BOUND_REF,
                url=f"{BASE}/serie/{BOUND_REF}",
                path=f"/serie/{BOUND_REF}",
                quality="1080p",
                language="MULTI",
                season=1,
                active=True,
                last_scanned_at=NOW.replace(tzinfo=None),
            )
        )
        commit_retry(db)
        provider_id = provider.id
    finally:
        db.close()

    _BindingFallbackSource.search_calls = 0
    _BindingFallbackSource.get_links_calls = 0
    _BindingFallbackSource.get_links_refs = []
    from mediatracker.core.source_poll_phased import network_call_count, reset_network_call_count

    reset_network_call_count()
    source = _BindingFallbackSource()
    factory = app.state.session_factory
    from mediatracker.core import source_poll_phased as phased_mod

    marked: list[str] = []
    real_mark = phased_mod._mark_binding_error

    def _spy_mark(db, media_id, provider_id, page, moment, message):
        marked.append(message)
        return real_mark(db, media_id, provider_id, page, moment, message)

    monkeypatch.setattr(phased_mod, "_mark_binding_error", _spy_mark)
    worker_poll_if_due(
        factory,
        app.state.settings,
        now=NOW,
        rng=random.Random(13),
        sources=[(provider_id, source)],
    )
    http_calls = network_call_count()
    assert http_calls == 3, f"1 get_links binding + 1 search + 1 get_links remplacement, obtenu {http_calls}"
    assert _BindingFallbackSource.get_links_refs[0] == BOUND_REF
    assert _BindingFallbackSource.search_calls == 1
    fresh = factory()
    try:
        media = fresh.get(Media, media_id)
        assert media is not None
        assert media.last_poll_result == "found"
        row = fresh.scalar(
            select(SourceBinding).where(
                SourceBinding.media_id == media_id,
                SourceBinding.season == 1,
                SourceBinding.active.is_(True),
            )
        )
        assert row is not None
        assert row.last_scanned_at is not None
        assert marked and "introuvable" in marked[0]
        assert not (row.last_error or "").strip(), "binding sain après _remember_page sur la page de remplacement"
    finally:
        fresh.close()


def test_binding_apply_retry_does_not_replay_http(client, app, monkeypatch):
    """Retry commit après collecte binding→search : même nombre d'appels HTTP."""
    from sqlalchemy.exc import OperationalError

    from mediatracker.core.source_poll_phased import network_call_count, reset_network_call_count

    media_id = _prepare_followed_series(client, app)
    db = app.state.session_factory()
    try:
        provider = db.query(Provider).filter(Provider.type == "wawacity").one()
        media = db.get(Media, media_id)
        media.next_source_poll_at = NOW.replace(tzinfo=None)
        db.add(
            SourceBinding(
                media_id=media_id,
                provider_id=provider.id,
                external_ref=BOUND_REF,
                url=f"{BASE}/serie/{BOUND_REF}",
                path=f"/serie/{BOUND_REF}",
                quality="1080p",
                language="MULTI",
                season=1,
                active=True,
                last_scanned_at=NOW.replace(tzinfo=None),
            )
        )
        commit_retry(db)
        provider_id = provider.id
    finally:
        db.close()

    reset_network_call_count()
    commits = {"n": 0}
    real = session_mod.commit_retry

    def flaky_commit(db, *, attempts=3):
        commits["n"] += 1
        if commits["n"] == 3:
            raise OperationalError("database is locked", {}, None)
        return real(db, attempts=attempts)

    monkeypatch.setattr(session_mod, "commit_retry", flaky_commit)
    source = _BindingFallbackSource()
    factory = app.state.session_factory
    worker_poll_if_due(
        factory,
        app.state.settings,
        now=NOW,
        rng=random.Random(17),
        sources=[(provider_id, source)],
    )
    expected = network_call_count()
    assert expected == 3
    assert commits["n"] >= 2
    assert network_call_count() == expected


def test_on_hold_during_network_skips_apply(client, app):
    """Relecture post-réseau : on_hold posé pendant la barrière ne doit pas appliquer les liens."""
    media_id = _prepare_followed_series(client, app)
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate, delay=NETWORK_HOLD_S)
    source = _source(handler, cache=HtmlCache(0))
    factory = app.state.session_factory
    prep = factory()
    try:
        provider_id = prep.query(Provider).filter(Provider.type == "wawacity").one().id
    finally:
        prep.close()

    def pause_while_blocked():
        assert network_gate.wait(timeout=3)
        hold = factory()
        try:
            row = hold.get(Media, media_id)
            assert row is not None
            row.on_hold = True
            commit_retry(hold)
        finally:
            hold.close()

    t = threading.Thread(target=pause_while_blocked)
    t.start()
    worker_poll_if_due(
        factory,
        app.state.settings,
        now=NOW,
        rng=random.Random(2),
        sources=[(provider_id, source)],
    )
    t.join(timeout=30)
    source.close()
    fresh = factory()
    try:
        media = fresh.get(Media, media_id)
        assert media is not None
        assert media.on_hold is True
        item_ids = list(fresh.scalars(select(MediaItem.id).where(MediaItem.media_id == media_id)))
        downloads = list(fresh.scalars(select(Download).where(Download.media_item_id.in_(item_ids))).all())
        assert downloads == []
    finally:
        fresh.close()


def test_poll_if_due_hive_return_drains_deferred_unfollow(client, app, monkeypatch):
    """Sortie Hive : journal unfollow en préparation + apply Hive mockée (worker)."""
    film_id, _series_id = _seed_obtained_film_plus_due_series(client, app)
    login(client)
    client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "hive_enabled": True,
                "download_source_order": "hive",
            }
        },
        headers=csrf(client),
    )
    from mediatracker.core.source_poll import PollReport
    from mediatracker.hive import source_poll as hive_sp

    def _fake_hive(db, media, settings, **kwargs):
        return PollReport(
            result="nothing",
            message="",
            episodes=0,
            queued=0,
            proposed=0,
            last_check_at=NOW,
            next_check_at=None,
        )

    monkeypatch.setattr(hive_sp, "run_hive_follow_poll", _fake_hive)
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.remote_links_source",
        lambda *a, **k: None,
    )
    worker_poll_if_due(app.state.session_factory, app.state.settings, now=NOW)
    fresh = app.state.session_factory()
    try:
        unfollowed = fresh.scalar(
            select(Event).where(Event.type == "media.unfollowed", Event.media_id == film_id)
        )
        assert unfollowed is not None
        assert fresh.get(Media, film_id).followed is False
    finally:
        fresh.close()


def test_flush_inside_savepoint_still_blocks_contention_regression(client, app):
    """Flush explicite avant réseau : la contention persiste (limite connue, pas la résolution)."""
    media_id = _prepare_followed_series(client, app)
    errors: list[str] = []
    start_gate = threading.Event()
    network_gate = threading.Event()
    handler = _slow_handler([], network_gate, delay=NETWORK_HOLD_S)
    worker = threading.Thread(
        target=_run_poll_in_session,
        args=(app, media_id, start_gate, network_gate, errors),
        kwargs={
            "handler": handler,
            "use_savepoint": True,
            "flush_before_network": True,
            "commit": False,
        },
    )
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "Film bloque")
    worker.join(timeout=60)
    assert not errors, errors
    assert elapsed >= NETWORK_HOLD_S * 0.85, f"expected lock wait, got {elapsed:.2f}s"


def test_follow_then_check_sequential_does_not_double_queue(client, app):
    """Deux /check séquentiels — sans preuve de course ; pas de doublement de file."""
    from tests.conftest import connect_alldebrid_for_tests

    connect_alldebrid_for_tests(app)
    media_id = _prepare_followed_series(client, app)
    calls: list[str] = []
    _poll(app, media_id, _pages(calls))
    assert calls
    db = app.state.session_factory()
    try:
        assert db.get(Media, media_id).followed is True
        item_ids = list(db.scalars(select(MediaItem.id).where(MediaItem.media_id == media_id)))
        downloads = list(
            db.scalars(select(Download).where(Download.media_item_id.in_(item_ids))).all()
        )
        assert len(downloads) <= 2
    finally:
        db.close()
    checked = client.post(f"/api/v1/media/{media_id}/check", headers=csrf(client))
    assert checked.status_code == 200, checked.text
    db = app.state.session_factory()
    try:
        item_ids = list(db.scalars(select(MediaItem.id).where(MediaItem.media_id == media_id)))
        after = list(db.scalars(select(Download).where(Download.media_item_id.in_(item_ids))).all())
        assert len(after) == len(downloads), "un second /check ne doit pas dupliquer la file"
    finally:
        db.close()

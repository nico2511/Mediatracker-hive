"""Gossip Hive worker : pas de HTTP sous SAVEPOINT parent, release SQLite avant réseau."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from tests.conftest import catalog_min_logical_video_bytes, csrf, login, write_sparse_video_file
from tests.test_hive_t3_peer_http import CREATOR_URL, _enable
from tests.test_source_poll_sqlite_release import FAST_WRITE_MAX_S, NETWORK_HOLD_S

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import HiveEntry, HiveFileFingerprint, HiveMember, Media, MediaItem, Setting
from mediatracker.db.session import SQLITE_BUSY_MS, commit_retry, is_sqlite_locked
from mediatracker.hive.catalog import sync_hive_catalog
from mediatracker.hive.gossip_burst import HIVE_BURST_STATE_KEY, burst_due_ruches
from mediatracker.hive.gossip_worker import (
    PeerExchangeTarget,
    hive_gossip_tick,
    worker_hive_gossip_pass,
)
from mediatracker.hive.share_policy import grant_share_intent
from mediatracker.library.scanner import remember_filed
from mediatracker.worker import _maybe_savepoint

NOW = datetime(2026, 11, 8, 21, 38, tzinfo=timezone.utc)


def _seed_catalog_sync_surface(client, app) -> tuple[bytes, str]:
    """Ruche + film partageable : sync_hive_catalog réel écrit le burst catalogue."""
    ruche_id, peer_url = _seed_gossip_peer(client, app)
    settings = app.state.settings
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, settings)
        folder = layout.movies_dir / "SyncDemo (2020) [tmdbid-9001]"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "sync-demo.bin"
        sync_size = catalog_min_logical_video_bytes()
        write_sparse_video_file(video, sync_size, prefix=b"s")
        media = Media(type="film", title="SyncDemo", year=2020, tmdb_id=9001, followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            state="downloaded",
            downloaded=True,
            file_path=str(video.resolve()),
            release_quality="HD",
            release_language="VFF",
        )
        db.add(item)
        db.flush()
        remember_filed(db, video.resolve(), media, item, NOW)
        from mediatracker.hive.chunk_digest import file_sha256_hex

        st = video.stat()
        sha = file_sha256_hex(video)
        grant_share_intent(db, item.id)
        db.merge(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(video.resolve()),
                size=sync_size,
                mtime_ns=getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)),
                sha256_hex=sha,
                partial_size=sync_size,
                updated_at=NOW,
            )
        )
        db.commit()
    finally:
        db.close()
    return ruche_id, peer_url


def _seed_gossip_peer(client, app) -> tuple[bytes, str]:
    _enable(client, "aa", CREATOR_URL)
    login(client)
    rid = client.post("/api/v1/hive/ruches", headers=csrf(client)).json()["ruche_id_hex"]
    ruche_id = bytes.fromhex(rid)
    db = app.state.session_factory()
    try:
        db.add(
            HiveMember(
                ruche_id=ruche_id,
                member_instance_id="hv1fake-fake-fake-fake-fake-fake-fa01",
                public_key_pem="",
                public_address="https://gossip-peer.exemple.fr",
            )
        )
        db.commit()
    finally:
        db.close()
    return ruche_id, "https://gossip-peer.exemple.fr"


def _install_slow_peer_exchange(monkeypatch, network_gate: threading.Event) -> None:
    import mediatracker.hive.gossip_worker as gw

    def _slow_exchange(db, settings, target, *, now):
        del db, settings, target, now
        network_gate.set()
        time.sleep(NETWORK_HOLD_S)

    monkeypatch.setattr(gw, "run_peer_exchange", _slow_exchange)
    monkeypatch.setattr(gw, "refresh_hive_catalogue_store", lambda *_a, **_k: None)


def _install_slow_peer_http(monkeypatch, network_gate: threading.Event):
    class _Resp:
        def __init__(self, status_code: int, body: dict | None = None) -> None:
            self.status_code = status_code
            self._body = body or {}

        def json(self):
            return self._body

    def _slow_fetch(*_a, **_k):
        network_gate.set()
        time.sleep(NETWORK_HOLD_S)
        return _Resp(404)

    def _slow_sync(*_a, **_k):
        network_gate.set()
        time.sleep(NETWORK_HOLD_S)
        return _Resp(200, {"entries": [], "vector": {}, "has_more": False})

    monkeypatch.setattr("mediatracker.hive.gossip_exchange.fetch_member_list", _slow_fetch)
    monkeypatch.setattr("mediatracker.hive.gossip_exchange.gossip_sync_once", _slow_sync)


def _write_during_network(factory, network_gate: threading.Event, title: str) -> float:
    assert network_gate.wait(timeout=5), "gossip n'est pas entré en phase réseau"
    session = factory()
    try:
        started = time.monotonic()
        session.add(
            Setting(
                key=f"probe-{title}",
                value="1",
                is_secret=False,
                updated_at=NOW,
            )
        )
        try:
            commit_retry(session)
        except OperationalError as exc:
            elapsed = time.monotonic() - started
            if is_sqlite_locked(exc):
                return elapsed
            raise
        return time.monotonic() - started
    finally:
        session.close()


def _run_gossip_savepoint(app, start_gate: threading.Event, errors: list[str], target: PeerExchangeTarget) -> None:
    factory = app.state.session_factory
    settings = app.state.settings
    parent = factory()
    try:
        parent.add(Setting(key="parent-tx-hold", value="1", is_secret=False, updated_at=NOW))
        parent.flush()
        start_gate.wait(timeout=5)

        def _tick() -> None:
            hive_gossip_tick(
                parent,
                settings,
                now=NOW,
                force=True,
                peer_targets=[target],
            )

        _maybe_savepoint(parent, "Hive gossip", _tick)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        parent.rollback()
        parent.close()


def _run_catalog_sync_then_gossip(
    app,
    start_gate: threading.Event,
    errors: list[str],
    target: PeerExchangeTarget,
    *,
    commit_catalog: bool,
) -> None:
    factory = app.state.session_factory
    settings = app.state.settings
    parent = factory()
    try:
        sync_hive_catalog(parent, settings, now=NOW)
        assert parent.scalar(select(HiveEntry).limit(1)) is not None, "sync_hive_catalog n'a pas publié"
        assert parent.get(Setting, HIVE_BURST_STATE_KEY) is not None
        if commit_catalog:
            commit_retry(parent)
        start_gate.set()
        worker_hive_gossip_pass(
            factory,
            settings,
            now=NOW,
            force=True,
            peer_targets=[target],
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        parent.rollback()
        parent.close()


def _run_gossip_isolated(app, start_gate: threading.Event, errors: list[str], target: PeerExchangeTarget) -> None:
    factory = app.state.session_factory
    settings = app.state.settings
    try:
        start_gate.wait(timeout=5)
        worker_hive_gossip_pass(
            factory,
            settings,
            now=NOW,
            force=True,
            peer_targets=[target],
        )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")


def test_savepoint_gossip_network_blocks_concurrent_writer(client, app, monkeypatch):
    """Rouge attendu : SAVEPOINT + réseau lent empêche un writer indépendant (régression documentée)."""
    ruche_id, peer_url = _seed_gossip_peer(client, app)
    target = PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=peer_url)
    network_gate = threading.Event()
    _install_slow_peer_http(monkeypatch, network_gate)

    errors: list[str] = []
    start_gate = threading.Event()
    worker = threading.Thread(
        target=_run_gossip_savepoint,
        args=(app, start_gate, errors, target),
    )
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "savepoint")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed >= FAST_WRITE_MAX_S, (
        f"écriture concurrente trop rapide ({elapsed:.2f}s) : le SAVEPOINT ne retient pas la base"
    )


def test_worker_gossip_isolated_allows_concurrent_writer_during_network(client, app, monkeypatch):
    """Writer SQLite indépendant pendant réseau gossip mock lent (fichier réel, session isolée)."""
    ruche_id, peer_url = _seed_gossip_peer(client, app)
    target = PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=peer_url)
    network_gate = threading.Event()
    _install_slow_peer_http(monkeypatch, network_gate)
    db_path = app.state.settings.database_url.removeprefix("sqlite:///")
    assert (db_path and db_path != ":memory:")

    errors: list[str] = []
    start_gate = threading.Event()
    worker = threading.Thread(
        target=_run_gossip_isolated,
        args=(app, start_gate, errors, target),
    )
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "isolated")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, (
        f"écriture bloquée {elapsed:.2f}s pendant gossip isolé (db={db_path})"
    )

    verify = sqlite3.connect(db_path, timeout=5)
    verify.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
    row = verify.execute("SELECT 1 FROM setting WHERE key='probe-isolated'").fetchone()
    verify.close()
    assert row is not None


def test_uncommitted_catalog_sync_blocks_concurrent_writer_during_gossip(client, app, monkeypatch):
    """Rouge : parent ouvert après sync_hive_catalog réel sans commit garde le verrou pendant HTTP."""
    ruche_id, peer_url = _seed_catalog_sync_surface(client, app)
    target = PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=peer_url)
    network_gate = threading.Event()
    _install_slow_peer_exchange(monkeypatch, network_gate)
    errors: list[str] = []
    start_gate = threading.Event()
    worker = threading.Thread(
        target=_run_catalog_sync_then_gossip,
        args=(app, start_gate, errors, target),
        kwargs={"commit_catalog": False},
    )
    worker.start()
    start_gate.wait(timeout=5)
    elapsed = _write_during_network(app.state.session_factory, network_gate, "catalog-open")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed >= FAST_WRITE_MAX_S


def test_committed_catalog_sync_then_gossip_allows_concurrent_writer(client, app, monkeypatch):
    """Vert : commit parent après sync ; writer indépendant + burst visible en session enfant."""
    ruche_id, peer_url = _seed_catalog_sync_surface(client, app)
    target = PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=peer_url)
    network_gate = threading.Event()
    _install_slow_peer_exchange(monkeypatch, network_gate)
    errors: list[str] = []
    start_gate = threading.Event()
    worker = threading.Thread(
        target=_run_catalog_sync_then_gossip,
        args=(app, start_gate, errors, target),
        kwargs={"commit_catalog": True},
    )
    worker.start()
    start_gate.wait(timeout=5)
    elapsed = _write_during_network(app.state.session_factory, network_gate, "catalog-commit")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S

    child = app.state.session_factory()
    try:
        assert child.get(Setting, HIVE_BURST_STATE_KEY) is not None
        due_at_burst = NOW + timedelta(seconds=61)
        assert ruche_id in burst_due_ruches(child, due_at_burst)
    finally:
        child.close()


def test_burst_runs_after_gossip_tick_failure(client, app, monkeypatch):
    """Signal gossip (burst) isolé même si le tick échoue (résilience SAVEPOINT distinct)."""
    import mediatracker.hive.gossip_worker as gw

    ruche_id, _ = _seed_catalog_sync_surface(client, app)
    db = app.state.session_factory()
    try:
        from mediatracker.hive.gossip_burst import mark_local_catalog_change

        mark_local_catalog_change(db, ruche_id=ruche_id, now=NOW - timedelta(seconds=120))
        commit_retry(db)
    finally:
        db.close()

    sent: list[bool] = []

    def _fail_tick(*_a, **_k):
        raise RuntimeError("gossip tick simulé")

    def _mock_hint(*_a, **_k):
        sent.append(True)
        return True

    monkeypatch.setattr(gw, "hive_gossip_tick", _fail_tick)
    monkeypatch.setattr("mediatracker.hive.gossip_burst.send_gossip_hint_to_peer", _mock_hint)
    gw.run_worker_hive_gossip_isolated(
        app.state.session_factory,
        app.state.settings,
        now=NOW,
    )
    assert sent, "burst aurait dû s'exécuter après échec du tick"


def test_worker_pass_real_sync_commits_before_isolated_gossip(client, app, monkeypatch):
    """Vraie sync_hive_catalog dans _run_worker_pass : commit avant gossip isolé."""
    import mediatracker.worker as worker_mod

    ruche_id, peer_url = _seed_catalog_sync_surface(client, app)
    network_gate = threading.Event()
    _install_slow_peer_http(monkeypatch, network_gate)
    phase: list[str] = []
    real_commit = commit_retry

    def _track_commit(db):
        if db.in_nested_transaction():
            return real_commit(db)
        phase.append("commit")
        return real_commit(db)

    monkeypatch.setattr(worker_mod, "commit_retry", _track_commit)
    monkeypatch.setattr(worker_mod, "tick", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_scan_library", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_sync_jellyfin", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_align_jellyfin", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_push_jellyfin", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "_poll_sources_isolated", lambda *_a, **_k: None)
    real_iso = worker_mod._hive_gossip_isolated

    def _iso(factory, settings):
        phase.append("gossip")
        real_iso(factory, settings)

    monkeypatch.setattr(worker_mod, "_hive_gossip_isolated", _iso)
    real_catalog = sync_hive_catalog

    def _catalog(db, settings, **kwargs):
        phase.append("sync")
        return real_catalog(db, settings, **kwargs)

    monkeypatch.setattr(worker_mod, "sync_hive_catalog", _catalog)

    db = app.state.session_factory()
    try:
        worker_mod._run_worker_pass(db, app.state.settings, app.state.session_factory)
    finally:
        db.close()

    sync_idx = phase.index("sync")
    gossip_idx = phase.index("gossip")
    commit_between = phase[sync_idx + 1 : gossip_idx]
    assert "commit" in commit_between, phase


def test_worker_pass_does_not_run_gossip_under_parent_savepoint(client, app, monkeypatch):
    """Le passage worker n'invoque plus gossip sous SAVEPOINT parent."""
    import mediatracker.worker as worker_mod

    gossip_savepoints: list[str] = []
    orig_savepoint = worker_mod._maybe_savepoint

    def _spy_savepoint(db, label, fn):
        if "gossip" in label.lower():
            gossip_savepoints.append(label)
        return orig_savepoint(db, label, fn)

    isolated_calls: list[bool] = []

    def _spy_isolated(factory, settings):
        isolated_calls.append(True)

    monkeypatch.setattr(worker_mod, "_maybe_savepoint", _spy_savepoint)
    monkeypatch.setattr(worker_mod, "_hive_gossip_isolated", _spy_isolated)
    monkeypatch.setattr(worker_mod, "tick", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_scan_library", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_sync_jellyfin", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_align_jellyfin", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "maybe_push_jellyfin", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "_poll_sources_isolated", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "sync_hive_catalog", lambda *_a, **_k: None)

    db = app.state.session_factory()
    try:
        worker_mod._run_worker_pass(db, app.state.settings, app.state.session_factory)
    finally:
        db.close()

    assert isolated_calls == [True]
    assert gossip_savepoints == []

"""Synchro Jellyfin : release SQLite avant appels API mock lents (fichier réel, writer indépendant)."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from tests.conftest import csrf, login
from tests.test_hive_gossip_sqlite_release import _write_during_network
from tests.test_source_poll_sqlite_release import FAST_WRITE_MAX_S, NETWORK_HOLD_S

from mediatracker.db.models import Setting
from mediatracker.db.session import SQLITE_BUSY_MS, commit_retry, release_sqlite_before_network
from mediatracker.integrations.jellyfin_sync import REFRESH_AT, maybe_sync_jellyfin, schedule_library_refresh
from mediatracker.worker import _maybe_session

NOW = datetime(2026, 11, 8, 22, 15, tzinfo=timezone.utc)


class _SlowJellyfin:
    def __init__(self, network_gate: threading.Event, *, delay: float = NETWORK_HOLD_S) -> None:
        self._gate = network_gate
        self._delay = delay
        self.items: list[dict] = []
        self.refreshed = 0
        self.closed = False

    def refresh_library(self) -> None:
        self._gate.set()
        time.sleep(self._delay)
        self.refreshed += 1

    def user_items(self, user_id: str) -> list[dict]:
        assert user_id == "user-1"
        self._gate.set()
        time.sleep(self._delay)
        return self.items

    def series_index(self, user_id: str) -> dict[str, dict]:
        return {}

    def users(self) -> list[dict]:
        return [{"Id": "user-1", "Name": "nico"}]

    def close(self) -> None:
        self.closed = True


def _configure_jellyfin(client) -> None:
    saved = client.patch(
        "/api/v1/settings",
        json={
            "values": {
                "jellyfin_url": "http://jellyfin.local:8096",
                "jellyfin_api_key": "jellyfin-test-key",
                "jellyfin_user_id": "user-1",
                "jellyfin_path_prefix": "/srv/media",
                "jellyfin_poll_interval_min": 30,
            }
        },
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text


def _seed_refresh_due(client, app) -> None:
    login(client)
    _configure_jellyfin(client)
    db = app.state.session_factory()
    try:
        schedule_library_refresh(db, NOW - timedelta(seconds=120))
        commit_retry(db)
    finally:
        db.close()


def _clear_poll_history(app) -> None:
    db = app.state.session_factory()
    try:
        for key in (
            "jellyfin.last_watched_sync_at",
            "jellyfin.poll_retry_at",
            "jellyfin.poll_fail_count",
        ):
            row = db.get(Setting, key)
            if row is not None:
                db.delete(row)
        commit_retry(db)
    finally:
        db.close()


def _hold_write_lock(db) -> None:
    """Écriture flushée non commitée : reproduit un worker après tick(commit=False)."""
    db.add(
        Setting(
            key="jellyfin-sync-tx-hold",
            value="1",
            is_secret=False,
            updated_at=NOW,
        )
    )
    db.flush()


def _run_maybe_sync(app, network_gate: threading.Event, errors: list[str], *, refresh: bool) -> None:
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        _hold_write_lock(db)
        slow = _SlowJellyfin(network_gate)
        maybe_sync_jellyfin(db, settings, now=NOW, client=slow)
        if refresh:
            assert slow.refreshed == 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        db.close()


def _run_worker_jellyfin(app, start_gate: threading.Event, network_gate: threading.Event, errors: list[str]) -> None:
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        start_gate.wait(timeout=5)
        _hold_write_lock(db)
        slow = _SlowJellyfin(network_gate)
        _maybe_session(
            db,
            "Synchro Jellyfin",
            lambda: maybe_sync_jellyfin(db, settings, now=NOW, client=slow),
        )
        commit_retry(db)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        db.close()


def test_release_sqlite_never_commits_inside_savepoint(app, monkeypatch):
    """Le helper ne doit pas commit sous begin_nested (garde worker SAVEPOINT)."""
    import mediatracker.db.session as session_mod

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


def test_jellyfin_poll_releases_lock_during_slow_user_items(client, app):
    """maybe_sync_jellyfin : user_items lent ne bloque pas un writer indépendant."""
    login(client)
    _configure_jellyfin(client)
    _clear_poll_history(app)
    network_gate = threading.Event()
    errors: list[str] = []
    worker = threading.Thread(
        target=_run_maybe_sync,
        args=(app, network_gate, errors),
        kwargs={"refresh": False},
    )
    worker.start()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "jellyfin-poll")
    worker.join(timeout=90)
    assert not worker.is_alive(), "synchro Jellyfin bloquée"
    assert not errors, errors
    db_path = app.state.settings.database_url.removeprefix("sqlite:///")
    assert db_path and db_path != ":memory:"
    assert elapsed < FAST_WRITE_MAX_S, (
        f"écriture bloquée {elapsed:.2f}s pendant user_items Jellyfin (db={db_path})"
    )

    verify = sqlite3.connect(db_path, timeout=5)
    verify.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
    assert verify.execute("SELECT 1 FROM setting WHERE key='probe-jellyfin-poll'").fetchone() is not None
    verify.close()


def test_jellyfin_refresh_releases_lock_during_slow_library_refresh(client, app):
    """Chemin refresh debounced : refresh_library lent libère le fichier SQLite."""
    _seed_refresh_due(client, app)
    network_gate = threading.Event()
    errors: list[str] = []
    worker = threading.Thread(
        target=_run_maybe_sync,
        args=(app, network_gate, errors),
        kwargs={"refresh": True},
    )
    worker.start()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "jellyfin-refresh")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, f"écriture bloquée {elapsed:.2f}s pendant refresh Jellyfin"

    db = app.state.session_factory()
    try:
        row = db.get(Setting, REFRESH_AT)
        assert row is not None
        assert not (row.value or "").strip()
    finally:
        db.close()


def test_jellyfin_poll_without_release_blocks_writer_during_network(client, app, monkeypatch):
    """Rouge : sans release, transaction flushée pendant user_items bloque un writer SQLite."""
    login(client)
    _configure_jellyfin(client)
    _clear_poll_history(app)
    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_sync.release_sqlite_before_network",
        lambda _db: None,
    )
    network_gate = threading.Event()
    errors: list[str] = []

    def _run_blocked() -> None:
        session = app.state.session_factory()
        try:
            _hold_write_lock(session)
            slow = _SlowJellyfin(network_gate)
            maybe_sync_jellyfin(session, app.state.settings, now=NOW, client=slow)
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            session.close()

    worker = threading.Thread(target=_run_blocked)
    worker.start()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "jellyfin-red")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed >= NETWORK_HOLD_S * 0.85, (
        f"écriture concurrente trop rapide ({elapsed:.2f}s) : attendu blocage sans release"
    )


def test_worker_maybe_sync_jellyfin_releases_lock_during_slow_poll(client, app):
    """Entrée worker réelle (_maybe_session + maybe_sync_jellyfin), pas de SAVEPOINT parent."""
    login(client)
    _configure_jellyfin(client)
    _clear_poll_history(app)
    network_gate = threading.Event()
    errors: list[str] = []
    start_gate = threading.Event()
    worker = threading.Thread(target=_run_worker_jellyfin, args=(app, start_gate, network_gate, errors))
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "worker-jellyfin")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, f"worker jellyfin_sync bloque concurrent ({elapsed:.2f}s)"

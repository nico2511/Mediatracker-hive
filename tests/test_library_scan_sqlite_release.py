"""Scan bibliothèque : release SQLite avant TMDB mock lent (fichier réel, writer indépendant)."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from tests.conftest import login
from tests.test_hive_gossip_sqlite_release import _write_during_network
from tests.test_source_poll_sqlite_release import FAST_WRITE_MAX_S, NETWORK_HOLD_S

from mediatracker.db.models import LibraryFile, Setting
from mediatracker.db.session import SQLITE_BUSY_MS, commit_retry, release_sqlite_before_network
from mediatracker.integrations.tmdb import TmdbHit, TmdbTitle
from mediatracker.library.scanner import LAST_SCAN_KEY, maybe_scan_library, scan_library
from mediatracker.worker import _maybe_session

NOW = datetime(2026, 11, 8, 22, 5, tzinfo=timezone.utc)
PROBE_TITLE = "Film-Probe-Reseau"
PROBE_TMDB = 88001


class _SlowTmdb:
    def __init__(self, network_gate: threading.Event, *, delay: float = NETWORK_HOLD_S) -> None:
        self._gate = network_gate
        self._delay = delay
        self.search_calls = 0
        self.fetch_calls = 0

    def search(self, query: str, media_type: str) -> list[TmdbHit]:
        self.search_calls += 1
        if query == PROBE_TITLE and media_type == "film":
            self._gate.set()
            time.sleep(self._delay)
            return [TmdbHit(PROBE_TMDB, "film", PROBE_TITLE, 2020, None, None)]
        return []

    def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
        self.fetch_calls += 1
        self._gate.set()
        time.sleep(self._delay)
        return TmdbTitle(tmdb_id, media_type, PROBE_TITLE, 2020, None, None, None, None, ())

    def close(self) -> None:
        return None


def _layout(app) -> tuple[Path, Path]:
    from mediatracker.core.storage import storage_layout

    db = app.state.session_factory()
    try:
        layout = storage_layout(db, app.state.settings)
        return layout.movies_dir, layout.series_dir
    finally:
        db.close()


def _seed_untagged_film(movies: Path) -> Path:
    movies.mkdir(parents=True, exist_ok=True)
    folder = movies / f"{PROBE_TITLE} (2020)"
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / f"{PROBE_TITLE} (2020).mkv"
    clip.write_bytes(b"probe-scan-bytes")
    return clip


def _seed_tagged_film(movies: Path) -> Path:
    movies.mkdir(parents=True, exist_ok=True)
    folder = movies / f"{PROBE_TITLE} Tag (2020) [tmdbid-{PROBE_TMDB}]"
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / f"{PROBE_TITLE} Tag (2020) [tmdbid-{PROBE_TMDB}].mkv"
    clip.write_bytes(b"probe-fetch-bytes")
    return clip


def _clear_last_scan(app) -> None:
    db = app.state.session_factory()
    try:
        row = db.get(Setting, LAST_SCAN_KEY)
        if row is not None:
            db.delete(row)
            commit_retry(db)
    finally:
        db.close()


def _run_scan_thread(app, network_gate: threading.Event, errors: list[str], *, tagged: bool) -> None:
    movies, series = _layout(app)
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    if tagged:
        _seed_tagged_film(movies)
    else:
        _seed_untagged_film(movies)
    tmdb = _SlowTmdb(network_gate)
    session = app.state.session_factory()
    try:
        scan_library(session, movies=movies, series=series, now=NOW, tmdb=tmdb, settings=app.state.settings)
        commit_retry(session)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        session.close()


def _run_worker_scan(app, start_gate: threading.Event, network_gate: threading.Event, errors: list[str]) -> None:
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        start_gate.wait(timeout=5)
        slow = _SlowTmdb(network_gate)
        _maybe_session(
            db,
            "Scan bibliothèque",
            lambda: maybe_scan_library(db, settings, now=NOW, tmdb=slow),
        )
        commit_retry(db)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        db.close()


def test_release_sqlite_never_commits_inside_savepoint(app, monkeypatch):
    """Le helper ne doit pas commit sous begin_nested (même garde que source_poll)."""
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


def test_scan_search_releases_lock_during_slow_tmdb(client, app):
    """scan_library : upsert puis recherche TMDB ne bloque pas un writer indépendant."""
    login(client)
    _clear_last_scan(app)
    network_gate = threading.Event()
    errors: list[str] = []
    worker = threading.Thread(target=_run_scan_thread, args=(app, network_gate, errors), kwargs={"tagged": False})
    worker.start()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "scan-search")
    worker.join(timeout=90)
    assert not worker.is_alive(), "scan bloqué"
    assert not errors, errors
    db_path = app.state.settings.database_url.removeprefix("sqlite:///")
    assert db_path and db_path != ":memory:"
    assert elapsed < FAST_WRITE_MAX_S, f"écriture bloquée {elapsed:.2f}s pendant recherche TMDB (db={db_path})"

    verify = sqlite3.connect(db_path, timeout=5)
    verify.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
    assert verify.execute("SELECT 1 FROM setting WHERE key='probe-scan-search'").fetchone() is not None
    verify.close()

    db = app.state.session_factory()
    try:
        row = db.scalar(select(LibraryFile).where(LibraryFile.parsed_title == PROBE_TITLE))
        assert row is not None
        assert row.status == "matched"
        assert row.tmdb_id == PROBE_TMDB
    finally:
        db.close()


def test_scan_fetch_releases_lock_during_slow_tmdb(client, app):
    """Chemin tag TMDB : fetch lent ne bloque pas un writer indépendant."""
    login(client)
    network_gate = threading.Event()
    errors: list[str] = []
    worker = threading.Thread(target=_run_scan_thread, args=(app, network_gate, errors), kwargs={"tagged": True})
    worker.start()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "scan-fetch")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, f"écriture bloquée {elapsed:.2f}s pendant fetch TMDB"


def test_worker_maybe_scan_releases_lock_during_slow_tmdb(client, app):
    """Entrée worker réelle (_maybe_session + maybe_scan_library) avec TMDB injecté."""
    login(client)
    movies, series = _layout(app)
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    _clear_last_scan(app)
    _seed_untagged_film(movies)
    network_gate = threading.Event()
    errors: list[str] = []
    start_gate = threading.Event()
    worker = threading.Thread(target=_run_worker_scan, args=(app, start_gate, network_gate, errors))
    worker.start()
    start_gate.set()
    elapsed = _write_during_network(app.state.session_factory, network_gate, "worker-scan")
    worker.join(timeout=90)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_WRITE_MAX_S, f"writer scan session bloque concurrent ({elapsed:.2f}s)"

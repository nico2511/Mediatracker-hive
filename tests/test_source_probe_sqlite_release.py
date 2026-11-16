"""Probe manuel des sources : pas de transaction d'écriture pendant le réseau."""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from mediatracker.core.download_sources_status import download_source_statuses
from mediatracker.core.source_probe import SourceProbeTarget, _ProbeOutcome, probe_download_sources
from mediatracker.db.session import sqlite_path

FAST_IMMEDIATE_WRITE_S = 0.35


def _immediate_write_during_network(
    db_path: Path,
    network_gate: threading.Event,
    write_done: threading.Event,
) -> float:
    assert network_gate.wait(timeout=3), "le probe n'est pas entré en phase réseau"
    started = time.monotonic()
    conn = sqlite3.connect(db_path, timeout=1)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS probe_lock_smoke (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO probe_lock_smoke (v) VALUES ('ok')")
        conn.commit()
    finally:
        conn.close()
        write_done.set()
    return time.monotonic() - started


def test_probe_download_sources_allows_concurrent_write_during_network(client, app, monkeypatch):
    del client
    network_gate = threading.Event()
    write_done = threading.Event()
    target = SourceProbeTarget(title="Film Démo", media_type="film", season=None, episode=None, imdb_id=None)

    def gated_probe(*_args, **_kwargs) -> _ProbeOutcome:
        network_gate.set()
        assert write_done.wait(timeout=3), "écriture concurrente absente"
        return _ProbeOutcome("found", 2, None)

    monkeypatch.setattr(
        "mediatracker.core.source_probe._effective_probe_order",
        lambda _db, _settings: ["wawacity", "comet"],
    )
    monkeypatch.setattr("mediatracker.core.source_probe._source_disabled", lambda *_a, **_k: None)
    monkeypatch.setattr("mediatracker.core.source_probe._default_profile", lambda _db: None)
    monkeypatch.setattr(
        "mediatracker.core.source_probe.legacy_comet_context",
        lambda *_a, **_k: (None, None),
    )
    monkeypatch.setattr("mediatracker.core.source_probe._probe_one", gated_probe)

    db_path = sqlite_path(app.state.settings.database_url)
    assert db_path is not None
    errors: list[str] = []
    rows_holder: list = []

    def run_probe() -> None:
        db = app.state.session_factory()
        try:
            when = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
            rows_holder.extend(
                probe_download_sources(db, app.state.settings, target, now=when, sources=["wawacity", "comet"]).results
            )
            db.commit()
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            db.close()

    worker = threading.Thread(target=run_probe, daemon=True)
    worker.start()
    elapsed = _immediate_write_during_network(db_path, network_gate, write_done)
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert not errors, errors
    assert elapsed < FAST_IMMEDIATE_WRITE_S, f"BEGIN IMMEDIATE bloqué {elapsed:.2f}s"
    assert len(rows_holder) == 2
    assert all(row.issue == "found" for row in rows_holder)

    verify = app.state.session_factory()
    try:
        bundle = download_source_statuses(verify, app.state.settings)
        by_name = {item.source: item for item in bundle.sources}
        assert by_name["wawacity"].last_probe_link_count == 2
        assert by_name["wawacity"].last_probe_issue == "found"
        assert by_name["comet"].last_probe_link_count == 2
    finally:
        verify.close()


def test_probe_persists_records_only_after_network_phases(client, app, monkeypatch):
    del client
    """Les traces probe / santé sont visibles après commit, pas pendant l'attente réseau."""
    network_gate = threading.Event()
    released = threading.Event()
    target = SourceProbeTarget(title="Série Démo S01E01", media_type="series", season=1, episode=1, imdb_id="tt0000001")

    def slow_probe(*_args, **_kwargs) -> _ProbeOutcome:
        network_gate.set()
        released.wait(timeout=3)
        return _ProbeOutcome("empty", 0, None)

    monkeypatch.setattr(
        "mediatracker.core.source_probe._effective_probe_order",
        lambda _db, _settings: ["wawacity"],
    )
    monkeypatch.setattr("mediatracker.core.source_probe._source_disabled", lambda *_a, **_k: None)
    monkeypatch.setattr("mediatracker.core.source_probe._default_profile", lambda _db: None)
    monkeypatch.setattr(
        "mediatracker.core.source_probe.legacy_comet_context",
        lambda *_a, **_k: (None, None),
    )
    monkeypatch.setattr("mediatracker.core.source_probe._probe_one", slow_probe)

    def run_probe() -> None:
        db = app.state.session_factory()
        try:
            probe_download_sources(db, app.state.settings, target, sources=["wawacity"])
            db.commit()
        finally:
            db.close()

    thread = threading.Thread(target=run_probe, daemon=True)
    thread.start()
    assert network_gate.wait(timeout=3)
    mid_db = app.state.session_factory()
    try:
        mid = download_source_statuses(mid_db, app.state.settings)
        wawa_mid = next(item for item in mid.sources if item.source == "wawacity")
        assert wawa_mid.last_probe_at is None
    finally:
        mid_db.close()
    released.set()
    thread.join(timeout=10)
    after_db = app.state.session_factory()
    try:
        after = download_source_statuses(after_db, app.state.settings)
        wawa_after = next(item for item in after.sources if item.source == "wawacity")
        assert wawa_after.last_probe_issue == "empty"
        assert wawa_after.last_probe_at is not None
    finally:
        after_db.close()

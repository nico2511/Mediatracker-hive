"""Worker : commit SQLite avant les étapes réseau Liens morts et Notifications."""

from __future__ import annotations

import mediatracker.worker as worker_mod
from mediatracker.db.session import commit_retry


def _stub_worker_noise(monkeypatch) -> None:
    """Neutralise les autres étapes pour isoler l'ordre commit / réseau."""
    for name in dir(worker_mod):
        if not name.startswith("maybe_") and name not in {
            "_poll_sources_isolated",
            "_hive_gossip_isolated",
            "_catchup_cherche_signals",
            "deliver_due",
            "tick",
            "flush_pending_source_observations",
        }:
            continue
        obj = getattr(worker_mod, name)
        if not callable(obj):
            continue
        if name in {"maybe_reverify_dead_links", "deliver_due"}:
            continue
        monkeypatch.setattr(worker_mod, name, lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "_poll_sources_isolated", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "_hive_gossip_isolated", lambda *_a, **_k: None)
    monkeypatch.setattr(worker_mod, "tick", lambda *_a, **_k: None)


def test_worker_pass_commits_before_dead_links_step(client, app, monkeypatch):
    del client
    phase: list[str] = []
    real_commit = commit_retry

    def _track_commit(db):
        if not db.in_nested_transaction():
            phase.append("commit")
        return real_commit(db)

    def _dead_links(*_a, **_k):
        phase.append("dead_links")

    monkeypatch.setattr(worker_mod, "commit_retry", _track_commit)
    monkeypatch.setattr(worker_mod, "maybe_reverify_dead_links", _dead_links)
    _stub_worker_noise(monkeypatch)

    db = app.state.session_factory()
    try:
        worker_mod._run_worker_pass(db, app.state.settings, app.state.session_factory)
    finally:
        db.close()

    dl_idx = phase.index("dead_links")
    assert "commit" in phase[:dl_idx], phase


def test_worker_pass_commits_before_notifications_step(client, app, monkeypatch):
    del client
    phase: list[str] = []
    real_commit = commit_retry

    def _track_commit(db):
        if not db.in_nested_transaction():
            phase.append("commit")
        return real_commit(db)

    def _deliver(*_a, **_k):
        phase.append("notifications")
        return 0

    monkeypatch.setattr(worker_mod, "commit_retry", _track_commit)
    monkeypatch.setattr(worker_mod, "deliver_due", _deliver)
    monkeypatch.setattr(worker_mod, "maybe_reverify_dead_links", lambda *_a, **_k: 0)
    _stub_worker_noise(monkeypatch)

    db = app.state.session_factory()
    try:
        worker_mod._run_worker_pass(db, app.state.settings, app.state.session_factory)
    finally:
        db.close()

    ntfy_idx = phase.index("notifications")
    assert "commit" in phase[:ntfy_idx], phase
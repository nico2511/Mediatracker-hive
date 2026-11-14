"""Hotfix : l'étape Pipeline du worker peut committer (lancement des téléchargements)."""

import logging

import mediatracker.worker as worker
from mediatracker.db.session import commit_retry


def test_pipeline_step_may_commit_several_times(client, app, monkeypatch, caplog):
    del client
    db = app.state.session_factory()
    calls = {"tick": 0}

    def fake_tick(session, _settings, commit=False):
        del commit
        # _start_pending commits before AllDebrid, then once per started item.
        commit_retry(session)
        commit_retry(session)
        commit_retry(session)
        calls["tick"] += 1

    monkeypatch.setattr(worker, "tick", fake_tick)
    for name in dir(worker):
        if name.startswith("maybe_") or name in {
            "pump_forced_source_check",
            "pump_forced_source_check_worker",
            "poll_if_due",
            "worker_poll_if_due",
            "_poll_sources_isolated",
            "deliver_due",
            "flush_pending_source_observations",
        }:
            target = getattr(worker, name)
            if callable(target):
                monkeypatch.setattr(worker, name, lambda *a, **k: False)
    with caplog.at_level(logging.ERROR, logger=worker.logger.name):
        worker._run_worker_pass(db, app.state.settings, app.state.session_factory)
    assert calls["tick"] == 1
    assert "Pipeline a échoué" not in caplog.text

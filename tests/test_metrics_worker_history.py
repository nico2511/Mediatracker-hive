"""Compteurs worker (H2) et historique SQLite (H3)."""

from datetime import datetime, timedelta, timezone

import pytest
from tests.conftest import login

from mediatracker.core.metrics.history import (
    BUCKET_MINUTES,
    RETENTION_DAYS,
    fetch_history,
    history_export_payload,
    maybe_purge_metrics_history,
    maybe_record_metrics_history,
    reset_metrics_history,
)
from mediatracker.core.metrics.worker_counters import (
    PERSIST_MIN_INTERVAL_S,
    SOURCE_COMET,
    SOURCE_WAWACITY,
    TASK_CLASSIFY,
    TASK_LIBRARY_SCAN,
    maybe_persist_worker_metrics,
    persist_worker_metrics,
    record_source_request,
    record_task_duration,
    reset_worker_metrics,
    track_task,
)
from mediatracker.db.models import MetricsHistory


@pytest.fixture(autouse=True)
def _reset_metrics(client, app):
    del client
    db = app.state.session_factory()
    try:
        reset_worker_metrics(db)
        reset_metrics_history(db)
        db.commit()
    finally:
        db.close()
    yield


def test_worker_counters_merge_and_expose(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        record_task_duration(TASK_LIBRARY_SCAN, 1.5)
        record_task_duration(TASK_LIBRARY_SCAN, 2.5)
        record_source_request(SOURCE_WAWACITY, error=False)
        record_source_request(SOURCE_COMET, error=True)
        persist_worker_metrics(db, commit=True)
    finally:
        db.close()
    body = client.get("/api/v1/health/metrics").json()
    tasks = body["instant"]["task_durations"]
    assert tasks[TASK_LIBRARY_SCAN]["count"] == 2
    assert tasks[TASK_LIBRARY_SCAN]["last_s"] == pytest.approx(2.5, abs=0.01)
    assert tasks[TASK_LIBRARY_SCAN]["max_s"] == pytest.approx(2.5, abs=0.01)
    assert tasks[TASK_LIBRARY_SCAN]["avg_s"] == pytest.approx(2.0, abs=0.01)
    sources = body["instant"]["source_requests"]
    assert sources[SOURCE_WAWACITY]["count"] == 1
    assert sources[SOURCE_WAWACITY]["errors"] == 0
    assert sources[SOURCE_COMET]["errors"] == 1


def test_maybe_persist_throttles_to_sixty_seconds(app, monkeypatch):
    from mediatracker.db.models import Setting

    now = {"t": 1000.0}
    monkeypatch.setattr(
        "mediatracker.core.metrics.worker_counters.time.monotonic",
        lambda: now["t"],
    )
    db = app.state.session_factory()
    try:
        record_source_request(SOURCE_WAWACITY, error=False)
        assert maybe_persist_worker_metrics(db) is True
        now["t"] = 1010.0
        record_source_request(SOURCE_WAWACITY, error=False)
        assert maybe_persist_worker_metrics(db) is False
        assert maybe_persist_worker_metrics(db, force=True) is True
        now["t"] = 1020.0
        record_source_request(SOURCE_COMET, error=True)
        assert maybe_persist_worker_metrics(db) is False
        now["t"] = 1081.0
        assert maybe_persist_worker_metrics(db) is True
        row = db.get(Setting, "worker.runtime_metrics")
        assert row is not None
        assert PERSIST_MIN_INTERVAL_S == 60.0
    finally:
        db.close()


def test_track_task_context_manager(app):
    from mediatracker.db.models import Setting

    db = app.state.session_factory()
    try:
        with track_task(TASK_CLASSIFY):
            pass
        persist_worker_metrics(db, commit=True)
        stored = db.get(Setting, "worker.runtime_metrics")
        assert stored is not None
        assert TASK_CLASSIFY in stored.value
    finally:
        db.close()


def test_metrics_history_record_and_fetch(app, settings):
    db = app.state.session_factory()
    try:
        now = datetime(2026, 1, 10, 12, 7, tzinfo=timezone.utc)
        assert maybe_record_metrics_history(db, settings, now=now) is True
        db.commit()
        rows = fetch_history(
            db,
            since=now - timedelta(hours=1),
            until=now + timedelta(minutes=1),
        )
        assert len(rows) == 1
        assert rows[0]["bucket_at"].startswith("2026-01-10T12:05:00")
        assert maybe_record_metrics_history(db, settings, now=now + timedelta(minutes=2)) is False
    finally:
        db.close()


def test_metrics_history_purge(app):
    from sqlalchemy import select

    db = app.state.session_factory()
    try:
        old = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS + 1)
        db.add(
            MetricsHistory(
                bucket_at=old,
                cpu_api=1.0,
                worker_wakes={},
                source_requests={},
                task_durations={},
            )
        )
        db.commit()
        removed = maybe_purge_metrics_history(db, now=datetime.now(timezone.utc))
        db.commit()
        assert removed == 1
        assert db.scalar(select(MetricsHistory.id)) is None
    finally:
        db.close()


def test_history_endpoints_require_auth(client):
    assert client.get("/api/v1/health/history").status_code == 401
    assert client.get("/api/v1/health/history/export").status_code == 401


def test_history_endpoints_window_and_export(client, app, settings):
    login(client)
    db = app.state.session_factory()
    try:
        moment = datetime.now(timezone.utc)
        maybe_record_metrics_history(db, settings, now=moment)
        db.commit()
    finally:
        db.close()
    history = client.get("/api/v1/health/history", params={"hours": 48})
    assert history.status_code == 200
    payload = history.json()
    assert payload["bucket_minutes"] == BUCKET_MINUTES
    assert len(payload["points"]) >= 1
    export = client.get("/api/v1/health/history/export", params={"hours": 48})
    assert export.status_code == 200
    assert export.headers["content-disposition"].startswith('attachment; filename="metrics-history-')
    body = export.json()
    assert body["bucket_minutes"] == BUCKET_MINUTES
    assert len(body["points"]) >= 1
    db = app.state.session_factory()
    try:
        assert history_export_payload(db, since=moment - timedelta(hours=1))["points"]
    finally:
        db.close()

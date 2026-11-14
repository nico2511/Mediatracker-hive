"""Worker wake FIFO, idle sleep cap, deadlines, and metrics."""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from tests.conftest import login

from mediatracker.core.worker_schedule import (
    ACTIVE_TICK_SECONDS,
    MAX_IDLE_SLEEP_SECONDS,
    compute_sleep_seconds,
    has_active_downloads,
    next_deadline,
)
from mediatracker.core.worker_wake import (
    REASON_API_MEDIA,
    REASON_CLASSIFY,
    WorkerWaiter,
    reset_wake_metrics,
    wake_counts,
    wake_worker,
)
from mediatracker.db.models import Download, Media, MediaItem, Setting


@pytest.fixture(autouse=True)
def _reset_wake_metrics(client, app):
    del client
    db = app.state.session_factory()
    try:
        reset_wake_metrics(db)
        db.commit()
    finally:
        db.close()
    yield


def test_wake_worker_records_reason_and_interrupts_wait(client, settings, app):
    del client
    db = app.state.session_factory()
    try:
        stop = threading.Event()
        waiter = WorkerWaiter(settings.config_dir, stop)
        waiter.start()
        started = time.monotonic()
        wake_worker(db, settings, REASON_API_MEDIA)
        waiter.wait(5.0)
        elapsed = time.monotonic() - started
        assert elapsed < 2.0
        assert wake_counts(db)[REASON_API_MEDIA] == 1
    finally:
        db.close()


def test_compute_sleep_caps_at_sixty_seconds(client, settings, app):
    del client
    from mediatracker.core.cleanup_classify import mark_classify_pass

    db = app.state.session_factory()
    try:
        mark_classify_pass(datetime.now(timezone.utc))
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        db.add(
            Setting(
                key="catalog.next_refresh_at",
                value=future.isoformat(),
                is_secret=False,
                updated_at=datetime.now(timezone.utc),
            )
        )
        db.commit()
        pause = compute_sleep_seconds(db, settings)
        assert pause == MAX_IDLE_SLEEP_SECONDS
    finally:
        db.close()


def test_compute_sleep_keeps_active_tick(client, settings, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(type="film", title="Actif", followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, title="Actif", state="downloading")
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="downloading", created_at=datetime.now(timezone.utc)))
        db.commit()
        assert has_active_downloads(db)
        assert compute_sleep_seconds(db, settings) == ACTIVE_TICK_SECONDS
    finally:
        db.close()


def test_next_deadline_returns_now_when_retry_due(client, settings, app):
    del client
    db = app.state.session_factory()
    try:
        now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
        media = Media(type="film", title="Dû", followed=True)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, title="Dû", state="missing")
        db.add(item)
        db.flush()
        db.add(
            Download(
                media_item_id=item.id,
                state="pending",
                next_retry_at=now - timedelta(seconds=30),
                created_at=now,
            )
        )
        db.commit()
        deadline = next_deadline(db, settings, now)
        assert deadline == now
    finally:
        db.close()


def test_classify_wake_reason(client, settings, app):
    del client
    from mediatracker.core.cleanup_classify import request_classify

    db = app.state.session_factory()
    try:
        request_classify(db, settings)
        db.commit()
        assert wake_counts(db)[REASON_CLASSIFY] == 1
    finally:
        db.close()


def test_worker_waiter_continues_when_mkfifo_unsupported(tmp_path, monkeypatch, caplog):
    stop = threading.Event()
    waiter = WorkerWaiter(tmp_path, stop)

    def _unsupported(*_args, **_kwargs):
        raise OSError(95, "Operation not supported")

    monkeypatch.setattr("mediatracker.core.worker_wake.os.mkfifo", _unsupported)
    with caplog.at_level(logging.WARNING, logger="mediatracker.core.worker_wake"):
        waiter.start()
    assert waiter._thread is None
    started = time.monotonic()
    waiter.wait(0.12)
    assert time.monotonic() - started >= 0.1
    assert waiter._fifo_warned
    assert "FIFO" in caplog.text


def test_worker_pause_falls_back_when_schedule_fails(client, settings, app, monkeypatch):
    del client
    import mediatracker.worker as worker_mod

    def _boom(_db, _settings):
        raise RuntimeError("scheduler unavailable")

    monkeypatch.setattr(worker_mod, "compute_sleep_seconds", _boom)
    pause = worker_mod.compute_worker_pause(app.state.session_factory, settings)
    assert pause == ACTIVE_TICK_SECONDS


def test_metrics_exposes_wake_counts(client, app):
    login(client)
    db = app.state.session_factory()
    try:
        wake_worker(db, app.state.settings, REASON_API_MEDIA)
    finally:
        db.close()
    body = client.get("/api/v1/health/metrics").json()
    assert body["instant"]["worker_wakes"][REASON_API_MEDIA] == 1

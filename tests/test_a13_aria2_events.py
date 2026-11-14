"""A13 — notifications aria2 WebSocket, réveil worker et cadence de sondage."""

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError
from tests.conftest import login

from mediatracker.core.aria2_events import (
    ACTIVE_TICK_FALLBACK,
    ACTIVE_TICK_WEBSOCKET,
    MODE_CONNECTED,
    MODE_FALLBACK,
    Aria2EventListener,
    effective_notify_mode,
    handle_notification,
    read_notify_mode,
    reset_notify_mode,
    rpc_url_to_websocket,
    set_runtime_notify_mode,
    write_notify_mode,
)
from mediatracker.core.worker_schedule import compute_sleep_seconds, has_active_downloads
from mediatracker.core.worker_wake import REASON_ARIA2_EVENT, reset_wake_metrics, wake_counts
from mediatracker.db.models import Download, Media, MediaItem


@pytest.fixture(autouse=True)
def _reset_aria2_notify_state(client, app):
    del client
    db = app.state.session_factory()
    try:
        reset_notify_mode(db)
        reset_wake_metrics(db)
        db.commit()
    finally:
        db.close()
    yield


def test_rpc_url_to_websocket_localhost():
    assert rpc_url_to_websocket("http://127.0.0.1:6800/jsonrpc") == "ws://127.0.0.1:6800/jsonrpc"


def test_write_notify_mode_skips_unchanged(client, app):
    del client
    db = app.state.session_factory()
    try:
        assert write_notify_mode(db, MODE_FALLBACK) is True
        db.commit()
        assert write_notify_mode(db, MODE_FALLBACK) is False
        assert write_notify_mode(db, MODE_CONNECTED) is True
        assert write_notify_mode(db, MODE_CONNECTED) is False
    finally:
        db.close()


def test_effective_notify_mode_prefers_runtime(client, app):
    del client
    db = app.state.session_factory()
    try:
        write_notify_mode(db, MODE_FALLBACK)
        db.commit()
        set_runtime_notify_mode(MODE_CONNECTED)
        assert effective_notify_mode(db) == MODE_CONNECTED
        _seed_active_download(db)
        from mediatracker.core.aria2_events import active_download_tick_seconds

        assert active_download_tick_seconds(db) == ACTIVE_TICK_WEBSOCKET
    finally:
        reset_notify_mode(db)
        db.commit()
        db.close()


def test_persist_mode_retries_on_sqlite_lock(settings, app):
    listener = Aria2EventListener(settings, app.state.session_factory, threading.Event())
    calls = {"n": 0}

    def _flaky_write(db, mode, *, commit=True):
        del db, commit
        calls["n"] += 1
        if calls["n"] < 2:
            raise OperationalError("UPDATE setting", {}, Exception("database is locked"))
        return True

    with patch("mediatracker.core.aria2_events.write_notify_mode", side_effect=_flaky_write):
        listener._persist_mode(MODE_FALLBACK)
    assert calls["n"] == 2
    assert listener._persisted_mode == MODE_FALLBACK


def test_listener_thread_survives_persist_lock(settings, app):
    stop = threading.Event()
    listener = Aria2EventListener(settings, app.state.session_factory, stop)

    def _always_locked(_db, _mode, *, commit=True):
        del _db, commit
        raise OperationalError("UPDATE setting", {}, Exception("database is locked"))

    listener._read_rpc = lambda: None  # type: ignore[method-assign]
    with patch("mediatracker.core.aria2_events.write_notify_mode", side_effect=_always_locked):
        listener.start()
        try:
            time.sleep(0.6)
            assert listener._thread is not None
            assert listener._thread.is_alive()
            db = app.state.session_factory()
            try:
                assert effective_notify_mode(db) == MODE_FALLBACK
            finally:
                db.close()
        finally:
            stop.set()
            listener.stop()


def test_notification_wakes_worker(client, settings, app):
    del client
    db = app.state.session_factory()
    try:
        payload = {"method": "aria2.onDownloadComplete", "params": ["faux-gid"]}
        assert handle_notification(db, settings, payload) is True
        db.commit()
        assert wake_counts(db)[REASON_ARIA2_EVENT] == 1
        ignored = {"method": "aria2.onDownloadStart", "params": ["faux-gid"]}
        assert handle_notification(db, settings, ignored) is False
    finally:
        db.close()


def _seed_active_download(db) -> None:
    media = Media(type="film", title="Brume", followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, title="Brume", state="downloading")
    db.add(item)
    db.flush()
    db.add(Download(media_item_id=item.id, state="downloading", created_at=datetime.now(timezone.utc)))
    db.commit()


def test_active_tick_fallback_when_disconnected(client, settings, app):
    del client
    db = app.state.session_factory()
    try:
        _seed_active_download(db)
        assert has_active_downloads(db)
        write_notify_mode(db, MODE_FALLBACK)
        assert compute_sleep_seconds(db, settings) == ACTIVE_TICK_FALLBACK
    finally:
        db.close()


def test_active_tick_relaxed_when_websocket_connected(client, settings, app):
    del client
    db = app.state.session_factory()
    try:
        _seed_active_download(db)
        write_notify_mode(db, MODE_CONNECTED)
        assert compute_sleep_seconds(db, settings) == ACTIVE_TICK_WEBSOCKET
    finally:
        db.close()


def test_metrics_exposes_notify_mode(client, app):
    del app
    login(client)
    body = client.get("/api/v1/health/metrics")
    assert body.status_code == 200
    mode = body.json()["instant"]["aria2"]["notify_mode"]
    assert mode in {MODE_FALLBACK, MODE_CONNECTED, None}


def test_listener_marks_fallback_on_connect_error(settings, app):
    class _FailingConnect:
        async def __aenter__(self):
            raise OSError("refused")

        async def __aexit__(self, *_args):
            return None

    stop = threading.Event()
    listener = Aria2EventListener(
        settings,
        app.state.session_factory,
        stop,
        connect=lambda _url: _FailingConnect(),
    )
    listener._read_rpc = lambda: ("ws://127.0.0.1:9/jsonrpc", "faux-secret")  # type: ignore[method-assign]
    listener.start()
    try:
        time.sleep(0.4)
        db = app.state.session_factory()
        try:
            assert read_notify_mode(db) == MODE_FALLBACK
        finally:
            db.close()
    finally:
        stop.set()
        listener.stop()


def test_listener_receives_event_and_sets_connected(settings, app):
    import websockets

    received = threading.Event()
    ready = threading.Event()
    port_holder: dict[str, int] = {}

    async def _serve() -> None:
        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            port_holder["port"] = server.sockets[0].getsockname()[1]
            ready.set()
            while not stop.is_set():
                await asyncio.sleep(0.1)

    async def _handler(websocket):
        await websocket.send(
            json.dumps({"method": "aria2.onDownloadError", "params": [{"gid": "faux-gid"}]})
        )
        received.set()
        while not stop.is_set():
            await asyncio.sleep(0.2)

    stop = threading.Event()
    server_thread = threading.Thread(
        target=lambda: asyncio.run(_serve()),
        name="fake-aria2-ws",
        daemon=True,
    )
    server_thread.start()
    assert ready.wait(timeout=5.0)
    ws_url = f"ws://127.0.0.1:{port_holder['port']}/jsonrpc"

    listener = Aria2EventListener(settings, app.state.session_factory, stop, connect=websockets.connect)
    listener._read_rpc = lambda: (ws_url, "faux-secret")  # type: ignore[method-assign]
    listener.start()
    try:
        assert received.wait(timeout=5.0)
        time.sleep(0.4)
        db = app.state.session_factory()
        try:
            assert read_notify_mode(db) == MODE_CONNECTED
            assert wake_counts(db).get(REASON_ARIA2_EVENT, 0) >= 1
        finally:
            db.close()
    finally:
        stop.set()
        listener.stop()
        server_thread.join(timeout=3.0)

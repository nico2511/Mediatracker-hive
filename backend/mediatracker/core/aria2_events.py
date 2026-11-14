"""aria2 JSON-RPC notifications over WebSocket (worker thread).

When connected, active download polling relaxes to 10 s; completions still wake
the worker immediately. On disconnect, polling falls back to 2 s with backoff
reconnect (5 s → 60 s max).
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.db.models import Provider, Setting
from mediatracker.db.session import commit_retry, is_sqlite_locked

logger = logging.getLogger(__name__)

NOTIFY_MODE_KEY = "aria2.notify_mode"
MODE_CONNECTED = "connected"
MODE_FALLBACK = "fallback"

ACTIVE_TICK_FALLBACK = 2.0
ACTIVE_TICK_WEBSOCKET = 10.0

BACKOFF_MIN_S = 5.0
BACKOFF_MAX_S = 60.0

_THREAD_RESTART_PAUSE_S = 0.5
_PERSIST_ATTEMPTS = 4
_PERSIST_BACKOFF_INITIAL_S = 0.05
_PERSIST_BACKOFF_MAX_S = 1.0

_WAKE_METHODS = frozenset(
    {
        "aria2.onDownloadComplete",
        "aria2.onDownloadError",
        "aria2.onDownloadStop",
        "aria2.onBtDownloadComplete",
    }
)

# Worker process only: last mode chosen by the listener (even if SQLite persist failed).
_runtime_notify_mode: str | None = None


def rpc_url_to_websocket(rpc_url: str) -> str:
    parts = urlsplit(rpc_url.strip())
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = parts.path or "/jsonrpc"
    return urlunsplit((scheme, parts.netloc, path, "", ""))


def set_runtime_notify_mode(mode: str) -> None:
    global _runtime_notify_mode
    if mode in {MODE_CONNECTED, MODE_FALLBACK}:
        _runtime_notify_mode = mode


def read_notify_mode(db: Session) -> str:
    row = db.get(Setting, NOTIFY_MODE_KEY)
    if row is None or not row.value:
        return MODE_FALLBACK
    value = row.value.strip()
    if value in {MODE_CONNECTED, MODE_FALLBACK}:
        return value
    return MODE_FALLBACK


def effective_notify_mode(db: Session) -> str:
    if _runtime_notify_mode in {MODE_CONNECTED, MODE_FALLBACK}:
        return _runtime_notify_mode
    return read_notify_mode(db)


def write_notify_mode(db: Session, mode: str, *, commit: bool = True) -> bool:
    """Persist notify mode. Returns True when a row was inserted or updated."""
    if mode not in {MODE_CONNECTED, MODE_FALLBACK}:
        mode = MODE_FALLBACK
    row = db.get(Setting, NOTIFY_MODE_KEY)
    if row is not None and (row.value or "").strip() == mode:
        return False
    moment = datetime.now(timezone.utc)
    if row is None:
        db.add(Setting(key=NOTIFY_MODE_KEY, value=mode, is_secret=False, updated_at=moment))
    else:
        row.value = mode
        row.updated_at = moment
    if commit:
        commit_retry(db)
    else:
        db.flush()
    return True


def reset_notify_mode(db: Session) -> None:
    global _runtime_notify_mode
    _runtime_notify_mode = None
    row = db.get(Setting, NOTIFY_MODE_KEY)
    if row is not None:
        db.delete(row)
    db.flush()


def active_download_tick_seconds(db: Session) -> float:
    if effective_notify_mode(db) == MODE_CONNECTED:
        return ACTIVE_TICK_WEBSOCKET
    return ACTIVE_TICK_FALLBACK


def handle_notification(
    db: Session,
    settings: Settings,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> bool:
    """Record a worker wake when ``payload`` is a relevant aria2 notification."""
    method = payload.get("method")
    if method not in _WAKE_METHODS:
        return False
    from mediatracker.core.worker_wake import REASON_ARIA2_EVENT, wake_worker

    wake_worker(db, settings, REASON_ARIA2_EVENT, commit=commit)
    return True


def _load_rpc(db: Session, settings: Settings) -> tuple[str, str] | None:
    row = db.scalar(select(Provider).where(Provider.kind == "downloader", Provider.type == "aria2"))
    if row is None:
        return None
    downloader = None
    try:
        from mediatracker.health_report import _instantiate

        downloader = _instantiate(row, settings)
        config = downloader.config  # type: ignore[attr-defined]
        secret = str(config.rpc_secret or "").strip()
        if not secret:
            return None
        return rpc_url_to_websocket(config.rpc_url), secret
    except Exception:
        return None
    finally:
        if downloader is not None:
            try:
                downloader.close()
            except Exception:
                pass


class Aria2EventListener:
    """Single background thread: connect, listen, reconnect with backoff."""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker,
        stop: threading.Event,
        *,
        connect: Callable[[str], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._factory = session_factory
        self._stop = stop
        self._connect = connect
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._persisted_mode: str | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._thread_main, name="aria2-events", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(lambda: None)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._thread = None

    def _thread_main(self) -> None:
        while not self._stop.is_set():
            try:
                asyncio.run(self._run())
            except Exception as exc:
                logger.warning(
                    "Fil aria2-events : redémarrage après erreur (%s).",
                    exc.__class__.__name__,
                )
                if not self._stop.is_set():
                    time.sleep(_THREAD_RESTART_PAUSE_S)

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        backoff = BACKOFF_MIN_S
        disconnect_warned = False
        while not self._stop.is_set():
            try:
                cfg = self._read_rpc()
                if cfg is None:
                    self._persist_mode(MODE_FALLBACK)
                    await self._sleep(BACKOFF_MIN_S)
                    continue
                ws_url, _secret = cfg
                try:
                    async with self._open(ws_url) as ws:
                        self._persist_mode(MODE_CONNECTED)
                        backoff = BACKOFF_MIN_S
                        disconnect_warned = False
                        while not self._stop.is_set():
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                            except TimeoutError:
                                continue
                            self._on_raw(raw)
                except Exception as exc:
                    self._persist_mode(MODE_FALLBACK)
                    if not disconnect_warned:
                        logger.warning(
                            "Notifications aria2 WebSocket indisponibles : repli sur le sondage (%s).",
                            exc.__class__.__name__,
                        )
                        disconnect_warned = True
                    await self._sleep(backoff)
                    backoff = min(backoff * 2, BACKOFF_MAX_S)
            except Exception as exc:
                logger.warning(
                    "Boucle aria2-events interrompue, poursuite (%s).",
                    exc.__class__.__name__,
                )
                self._persist_mode(MODE_FALLBACK)
                await self._sleep(BACKOFF_MIN_S)

    def _open(self, ws_url: str):
        if self._connect is not None:
            return self._connect(ws_url)
        import websockets

        return websockets.connect(ws_url, ping_interval=20, ping_timeout=20, close_timeout=3)

    def _read_rpc(self) -> tuple[str, str] | None:
        db = self._factory()
        try:
            return _load_rpc(db, self._settings)
        finally:
            db.close()

    def _persist_mode(self, mode: str) -> None:
        set_runtime_notify_mode(mode)
        if mode == self._persisted_mode:
            return
        delay = _PERSIST_BACKOFF_INITIAL_S
        for attempt in range(_PERSIST_ATTEMPTS):
            db = self._factory()
            try:
                if write_notify_mode(db, mode, commit=True):
                    self._persisted_mode = mode
                else:
                    self._persisted_mode = mode
                return
            except OperationalError as exc:
                db.rollback()
                if not is_sqlite_locked(exc):
                    logger.warning(
                        "aria2.notify_mode : écriture impossible (%s).",
                        exc.__class__.__name__,
                    )
                    return
                if attempt + 1 >= _PERSIST_ATTEMPTS:
                    logger.warning(
                        "aria2.notify_mode : base occupée après %s essais ; mode gardé en mémoire.",
                        _PERSIST_ATTEMPTS,
                    )
                    return
                time.sleep(delay)
                delay = min(delay * 2, _PERSIST_BACKOFF_MAX_S)
            except Exception as exc:
                db.rollback()
                logger.warning(
                    "aria2.notify_mode : écriture impossible (%s).",
                    exc.__class__.__name__,
                )
                return
            finally:
                db.close()

    def _on_raw(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        db = self._factory()
        try:
            try:
                handle_notification(db, self._settings, payload, commit=True)
            except OperationalError as exc:
                db.rollback()
                if is_sqlite_locked(exc):
                    logger.warning(
                        "Notification aria2 : base occupée, réveil ignoré (%s).",
                        exc.__class__.__name__,
                    )
                else:
                    logger.warning(
                        "Notification aria2 : traitement impossible (%s).",
                        exc.__class__.__name__,
                    )
        finally:
            db.close()

    async def _sleep(self, seconds: float) -> None:
        end = asyncio.get_running_loop().time() + seconds
        while not self._stop.is_set():
            remaining = end - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 0.5))

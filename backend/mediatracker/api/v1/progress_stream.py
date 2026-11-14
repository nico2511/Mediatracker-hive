"""Shared SSE progress hub: one snapshot per second for every connected client."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from queue import Empty, Full, Queue
from typing import Any

from mediatracker.config import Settings


@dataclass
class _Subscriber:
    media_id: int | None
    queue: Queue[dict[str, Any]]
    sent: int = 0
    limit: int | None = None


class SharedProgressStream:
    """Broadcast download progress (and optional fiche state) at most once per second."""

    def __init__(
        self,
        *,
        snapshot_builder: Callable[[set[int]], dict[str, Any]],
        tick_seconds: float = 1.0,
    ) -> None:
        self._snapshot_builder = snapshot_builder
        self._tick_seconds = tick_seconds
        self._lock = threading.Lock()
        self._subscribers: list[_Subscriber] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.snapshot_ticks: int = 0

    def subscribe(self, media_id: int | None, limit: int | None = None) -> _Subscriber:
        sub = _Subscriber(media_id=media_id, queue=Queue(maxsize=2), limit=limit)
        with self._lock:
            self._subscribers.append(sub)
            alive = self._thread is not None and self._thread.is_alive()
            if not alive:
                self._stop.clear()
                worker = threading.Thread(target=self._run, name="sse-progress", daemon=True)
                self._thread = worker
                worker.start()
        return sub

    def unsubscribe(self, sub: _Subscriber) -> None:
        with self._lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass

    def wait_payload(self, sub: _Subscriber, timeout: float) -> dict[str, Any] | None:
        try:
            return sub.queue.get(timeout=timeout)
        except Empty:
            return None

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if not self._subscribers:
                    break
                media_ids = {row.media_id for row in self._subscribers if row.media_id is not None}
            started = time.monotonic()
            try:
                payload = self._snapshot_builder(media_ids)
            except Exception:
                payload = {
                    "queue_paused": False,
                    "queue_held": False,
                    "queue_code": None,
                    "queue_message": None,
                    "items": [],
                    "downloads": [],
                }
            self.snapshot_ticks += 1
            with self._lock:
                targets = list(self._subscribers)
            for sub in targets:
                try:
                    sub.queue.put_nowait(payload)
                except Full:
                    try:
                        sub.queue.get_nowait()
                    except Empty:
                        pass
                    try:
                        sub.queue.put_nowait(payload)
                    except Full:
                        pass
            elapsed = time.monotonic() - started
            delay = max(0.0, self._tick_seconds - elapsed)
            if self._stop.wait(delay):
                break
        with self._lock:
            self._thread = None

    def shutdown(self) -> None:
        self._stop.set()


def format_progress_event(payload: dict[str, Any]) -> str:
    return f"event: progress\ndata: {json.dumps(payload)}\n\n"


def hub_for_app(app) -> SharedProgressStream:
    hub = getattr(app.state, "progress_stream", None)
    if hub is None:
        factory = app.state.session_factory
        settings: Settings = app.state.settings

        def build(media_ids: set[int]) -> dict[str, Any]:
            from mediatracker.api.v1.events import progress_snapshot_payload
            from mediatracker.api.v1.media import fiche_sse_payload

            db = factory()
            try:
                payload = progress_snapshot_payload(db)
                if media_ids:
                    fiches: dict[str, Any] = {}
                    for media_id in sorted(media_ids):
                        fiches[str(media_id)] = fiche_sse_payload(db, settings, media_id)
                    payload["fiches"] = fiches
                return payload
            finally:
                db.close()

        hub = SharedProgressStream(snapshot_builder=build)
        app.state.progress_stream = hub
    return hub

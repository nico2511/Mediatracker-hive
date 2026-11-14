"""Cross-process worker wake: SQLite counters + a config-dir FIFO signal.

The API and the worker are separate processes. Producers bump a persisted wake
counter (for metrics) and write one byte to a named pipe the worker owns. If the
worker is not running, the SQLite side still records the reason; the next start
picks up work on the first loop.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting

logger = logging.getLogger(__name__)

WAKE_COUNTS_KEY = "worker.wake_counts"
WAKE_SEQ_KEY = "worker.wake_seq"
FIFO_NAME = "worker.wake.fifo"

# Reasons used in metrics and tests (extend as needed).
REASON_API_QUEUE = "api.queue"
REASON_API_MEDIA = "api.media"
REASON_API_LIBRARY = "api.library"
REASON_API_JELLYFIN = "api.jellyfin"
REASON_API_HA = "api.ha"
REASON_API_HIVE = "api.hive"
REASON_HIVE_FINGERPRINT = "hive.fingerprint"
REASON_CLASSIFY = "classify"
REASON_SCHEDULE = "schedule.deadline"
REASON_ARIA2_EVENT = "aria2_event"
REASON_LIBRARY_INOTIFY = "library.inotify"


def fifo_path(settings: Settings | None = None, config_dir: Path | None = None) -> Path:
    base = config_dir if config_dir is not None else Path((settings or _default_settings()).config_dir)
    return base / FIFO_NAME


def reset_wake_metrics(db: Session) -> None:
    """Clear persisted wake counters. Tests call this between cases."""
    for key in (WAKE_COUNTS_KEY, WAKE_SEQ_KEY):
        row = db.get(Setting, key)
        if row is not None:
            db.delete(row)
    db.flush()


def wake_counts(db: Session) -> dict[str, int]:
    row = db.get(Setting, WAKE_COUNTS_KEY)
    if row is None or not row.value:
        return {}
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def wake_worker(
    db: Session,
    settings: Settings,
    reason: str,
    *,
    commit: bool = True,
) -> None:
    """Record a wake by reason and poke the worker FIFO when possible."""
    moment = datetime.now(timezone.utc)
    counts = wake_counts(db)
    counts[reason] = counts.get(reason, 0) + 1
    _put(db, WAKE_COUNTS_KEY, json.dumps(counts, sort_keys=True), moment)
    seq = _int_setting(db, WAKE_SEQ_KEY) + 1
    _put(db, WAKE_SEQ_KEY, str(seq), moment)
    if commit:
        db.commit()
    else:
        db.flush()
    _poke_fifo(fifo_path(settings))


def _poke_fifo(path: Path) -> None:
    try:
        if not path.exists():
            return
        fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, b"w")
        finally:
            os.close(fd)
    except OSError:
        # Worker not listening yet, or FIFO absent in tests.
        return


class WorkerWaiter:
    """Interruptible sleep: stop event, external wake FIFO, or timeout."""

    def __init__(self, config_dir: Path, stop: threading.Event) -> None:
        self._stop = stop
        self._wake = threading.Event()
        self._fifo = config_dir / FIFO_NAME
        self._thread: threading.Thread | None = None
        self._fifo_usable = False
        self._fifo_warned = False

    def start(self) -> None:
        if self._ensure_fifo():
            self._thread = threading.Thread(target=self._read_loop, name="worker-wake", daemon=True)
            self._thread.start()

    def wait(self, timeout: float) -> None:
        if timeout <= 0:
            return
        deadline = _monotonic() + timeout
        while not self._stop.is_set():
            remaining = deadline - _monotonic()
            if remaining <= 0:
                return
            if self._wake.wait(timeout=min(remaining, 5.0)):
                self._wake.clear()
                return

    def _ensure_fifo(self) -> bool:
        """Create the wake FIFO when the filesystem allows it."""
        if self._fifo_usable:
            return True
        self._fifo.parent.mkdir(parents=True, exist_ok=True)
        if self._fifo.exists():
            self._fifo_usable = True
            return True
        try:
            os.mkfifo(self._fifo, 0o600)
        except FileExistsError:
            self._fifo_usable = True
            return True
        except OSError as exc:
            if not self._fifo_warned:
                logger.warning(
                    "FIFO de réveil worker indisponible (%s) : sommeil par timeout uniquement.",
                    exc,
                )
                self._fifo_warned = True
            return False
        self._fifo_usable = True
        return True

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                with self._fifo.open("rb") as handle:
                    while not self._stop.is_set():
                        chunk = handle.read(1)
                        if not chunk:
                            break
                        self._wake.set()
            except OSError:
                if self._stop.wait(1.0):
                    return


def _put(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def _int_setting(db: Session, key: str) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return 0
    try:
        return int(row.value)
    except ValueError:
        return 0


def _default_settings() -> Settings:
    from mediatracker.config import get_settings

    return get_settings()


def _monotonic() -> float:
    import time

    return time.monotonic()

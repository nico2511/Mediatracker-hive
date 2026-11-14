"""In-memory worker counters flushed to SQLite (not on every instrumentation call)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from mediatracker.db.models import Setting
from mediatracker.db.session import is_sqlite_locked

PERSIST_KEY = "worker.runtime_metrics"

TASK_VERIFY_ALL = "verify_all"
TASK_LIBRARY_SCAN = "library_scan"
TASK_CLASSIFY = "classify"
TASK_JELLYFIN_SYNC = "jellyfin_sync"

SOURCE_WAWACITY = "wawacity"
SOURCE_FREE_TELECHARGER = "free_telecharger"
SOURCE_COMET = "comet"
SOURCE_TORRENTIO = "torrentio"
SOURCE_ALLDEBRID = "alldebrid"

PERSIST_MIN_INTERVAL_S = 60.0

_BACKGROUND_TASKS = frozenset({TASK_LIBRARY_SCAN, TASK_CLASSIFY, TASK_VERIFY_ALL})

_LOCK = threading.Lock()
_pending_tasks: dict[str, _DurationPending] = {}
_pending_sources: dict[str, _SourcePending] = {}
_active_tasks: dict[str, int] = {}
_last_persist_monotonic: float = 0.0


@dataclass
class _DurationPending:
    count: int = 0
    last_s: float = 0.0
    sum_s: float = 0.0
    max_s: float = 0.0

    def record(self, seconds: float) -> None:
        self.count += 1
        self.last_s = seconds
        self.sum_s += seconds
        if seconds > self.max_s:
            self.max_s = seconds


@dataclass
class _SourcePending:
    count: int = 0
    errors: int = 0

    def record(self, *, error: bool) -> None:
        self.count += 1
        if error:
            self.errors += 1


def reset_worker_metrics(db: Session) -> None:
    """Clear persisted and in-memory counters. Tests call this between cases."""
    global _last_persist_monotonic
    with _LOCK:
        _pending_tasks.clear()
        _pending_sources.clear()
        _active_tasks.clear()
        _last_persist_monotonic = 0.0
    row = db.get(Setting, PERSIST_KEY)
    if row is not None:
        db.delete(row)
    db.flush()


def is_task_active(*names: str) -> bool:
    with _LOCK:
        if not names:
            return bool(_active_tasks)
        return any(_active_tasks.get(name, 0) > 0 for name in names)


@contextmanager
def track_task(name: str, *, low_priority: bool | None = None) -> Iterator[None]:
    """Measure one worker task pass. No disk I/O."""
    from mediatracker.core.background_priority import background_priority

    if low_priority is None:
        low_priority = name in _BACKGROUND_TASKS
    process_wide = bool(low_priority and name != TASK_VERIFY_ALL)
    started = time.monotonic()
    with _LOCK:
        _active_tasks[name] = _active_tasks.get(name, 0) + 1
    try:
        if low_priority:
            with background_priority(process_wide=process_wide):
                yield
        else:
            yield
    finally:
        with _LOCK:
            count = _active_tasks.get(name, 0) - 1
            if count <= 0:
                _active_tasks.pop(name, None)
            else:
                _active_tasks[name] = count
        elapsed = time.monotonic() - started
        record_task_duration(name, elapsed)


def record_task_duration(name: str, seconds: float) -> None:
    with _LOCK:
        bucket = _pending_tasks.setdefault(name, _DurationPending())
        bucket.record(seconds)


def record_source_request(source: str, *, error: bool = False) -> None:
    with _LOCK:
        bucket = _pending_sources.setdefault(source, _SourcePending())
        bucket.record(error=error)
    from mediatracker.core.source_state import note_tracked_source_signal

    note_tracked_source_signal(source, up=not error)


def _remerge_pending(snapshot: dict[str, Any]) -> None:
    for name, row in (snapshot.get("tasks") or {}).items():
        bucket = _pending_tasks.setdefault(name, _DurationPending())
        try:
            count = int(row.get("count") or 0)
            last_s = float(row.get("last_s") or 0)
            sum_s = float(row.get("sum_s") or 0)
            max_s = float(row.get("max_s") or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        bucket.count += count
        bucket.last_s = last_s
        bucket.sum_s += sum_s
        if max_s > bucket.max_s:
            bucket.max_s = max_s
    for name, row in (snapshot.get("sources") or {}).items():
        bucket = _pending_sources.setdefault(name, _SourcePending())
        try:
            count = int(row.get("count") or 0)
            errors = int(row.get("errors") or 0)
        except (TypeError, ValueError):
            continue
        bucket.count += count
        bucket.errors += errors


def maybe_persist_worker_metrics(db: Session, *, commit: bool = False, force: bool = False) -> bool:
    """Flush pending counters when dirty, at most every ``PERSIST_MIN_INTERVAL_S`` unless ``force``."""
    global _last_persist_monotonic
    with _LOCK:
        if not _pending_tasks and not _pending_sources:
            return False
        if not force and (time.monotonic() - _last_persist_monotonic) < PERSIST_MIN_INTERVAL_S:
            return False
        pending_snapshot = _snapshot_pending()
        _pending_tasks.clear()
        _pending_sources.clear()
    merged = _merge(load_worker_metrics(db), pending_snapshot)
    try:
        _save(db, merged)
        if commit:
            db.commit()
        else:
            db.flush()
        _last_persist_monotonic = time.monotonic()
    except OperationalError as exc:
        if is_sqlite_locked(exc):
            with _LOCK:
                _remerge_pending(pending_snapshot)
            return False
        with _LOCK:
            _remerge_pending(pending_snapshot)
        raise
    return True


def persist_worker_metrics(db: Session, *, commit: bool = False) -> bool:
    """Immediate flush (verify-all end, tests)."""
    return maybe_persist_worker_metrics(db, commit=commit, force=True)


def load_worker_metrics(db: Session) -> dict[str, Any]:
    row = db.get(Setting, PERSIST_KEY)
    if row is None or not row.value:
        return {"tasks": {}, "sources": {}}
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return {"tasks": {}, "sources": {}}
    if not isinstance(raw, dict):
        return {"tasks": {}, "sources": {}}
    tasks = raw.get("tasks") if isinstance(raw.get("tasks"), dict) else {}
    sources = raw.get("sources") if isinstance(raw.get("sources"), dict) else {}
    return {"tasks": _normalize_tasks(tasks), "sources": _normalize_sources(sources)}


def public_snapshot(db: Session | None) -> dict[str, Any]:
    """Merged view for the metrics endpoint."""
    pending = _snapshot_pending()
    stored = load_worker_metrics(db) if db is not None else {"tasks": {}, "sources": {}}
    merged = _merge(stored, pending)
    return {
        "task_durations": merged["tasks"],
        "source_requests": merged["sources"],
    }


def _snapshot_pending() -> dict[str, Any]:
    tasks = {key: _duration_dict(value) for key, value in _pending_tasks.items()}
    sources = {key: _source_dict(value) for key, value in _pending_sources.items()}
    return {"tasks": tasks, "sources": sources}


def _merge(stored: dict[str, Any], pending: dict[str, Any]) -> dict[str, Any]:
    tasks = dict(stored.get("tasks") or {})
    for name, row in (pending.get("tasks") or {}).items():
        tasks[name] = _merge_duration(tasks.get(name), row)
    sources = dict(stored.get("sources") or {})
    for name, row in (pending.get("sources") or {}).items():
        sources[name] = _merge_source(sources.get(name), row)
    return {"tasks": tasks, "sources": sources}


def _merge_duration(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    if left is None:
        return dict(right)
    count = int(left.get("count") or 0) + int(right.get("count") or 0)
    sum_s = float(left.get("sum_s") or 0) + float(right.get("sum_s") or 0)
    max_s = max(float(left.get("max_s") or 0), float(right.get("max_s") or 0))
    last_s = float(right.get("last_s") or left.get("last_s") or 0)
    return _duration_out(count, last_s, sum_s, max_s)


def _merge_source(left: dict[str, Any] | None, right: dict[str, Any]) -> dict[str, Any]:
    if left is None:
        return dict(right)
    return {
        "count": int(left.get("count") or 0) + int(right.get("count") or 0),
        "errors": int(left.get("errors") or 0) + int(right.get("errors") or 0),
    }


def _save(db: Session, payload: dict[str, Any]) -> None:
    from datetime import datetime, timezone

    moment = datetime.now(timezone.utc)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    row = db.get(Setting, PERSIST_KEY)
    if row is None:
        db.add(Setting(key=PERSIST_KEY, value=text, is_secret=False, updated_at=moment))
        return
    row.value = text
    row.updated_at = moment


def _duration_dict(pending: _DurationPending) -> dict[str, Any]:
    return _duration_out(pending.count, pending.last_s, pending.sum_s, pending.max_s)


def _duration_out(count: int, last_s: float, sum_s: float, max_s: float) -> dict[str, Any]:
    avg_s = (sum_s / count) if count else None
    return {
        "count": count,
        "last_s": round(last_s, 4) if count else None,
        "avg_s": round(avg_s, 4) if avg_s is not None else None,
        "max_s": round(max_s, 4) if count else None,
        "sum_s": sum_s,
    }


def _source_dict(pending: _SourcePending) -> dict[str, Any]:
    return {"count": pending.count, "errors": pending.errors}


def _normalize_tasks(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, row in raw.items():
        if not isinstance(row, dict):
            continue
        try:
            count = int(row.get("count") or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0:
            continue
        try:
            last_s = float(row["last_s"]) if row.get("last_s") is not None else 0.0
            sum_s = float(row.get("sum_s") or 0)
            max_s = float(row.get("max_s") or 0)
        except (TypeError, ValueError):
            continue
        out[str(name)] = _duration_out(count, last_s, sum_s, max_s)
    return out


def _normalize_sources(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, row in raw.items():
        if not isinstance(row, dict):
            continue
        try:
            count = int(row.get("count") or 0)
            errors = int(row.get("errors") or 0)
        except (TypeError, ValueError):
            continue
        if count <= 0 and errors <= 0:
            continue
        out[str(name)] = {"count": count, "errors": errors}
    return out

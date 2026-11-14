"""In-memory metrics: /proc processes, aria2 global stat, HTTP aggregates."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.metrics.buffer import CircularSeries
from mediatracker.core.metrics.http import HttpMetricsRegistry
from mediatracker.core.metrics.proc import (
    ProcessMetrics,
    ProcTimes,
    proc_root,
    resolve_role_pids,
    sample_process,
)
from mediatracker.db.models import Provider
from mediatracker.providers.downloaders.aria2 import Aria2Client

DEFAULT_INTERVAL_S = 20.0
DEFAULT_HISTORY_S = 3600.0


@dataclass
class _ProcState:
    times: ProcTimes | None = None
    jiffies: int | None = None


@dataclass
class MetricsCollector:
    http: HttpMetricsRegistry = field(default_factory=HttpMetricsRegistry)
    interval_s: float = DEFAULT_INTERVAL_S
    history_s: float = DEFAULT_HISTORY_S
    _series: CircularSeries = field(init=False)
    _last_sample_at: float = 0.0
    _proc_prev: dict[str, _ProcState] = field(default_factory=dict)
    _proc_root: Callable[[], Any] = field(default=lambda: proc_root())

    def __post_init__(self) -> None:
        capacity = max(1, int(self.history_s / self.interval_s) + 1)
        self._series = CircularSeries(capacity)

    def maybe_sample(self, db: Session | None, settings: Settings | None) -> None:
        now = time.time()
        if now - self._last_sample_at < self.interval_s:
            return
        self._last_sample_at = now
        payload = self._build_point(db, settings)
        self._series.append(now, payload)

    def snapshot(self, db: Session | None, settings: Settings | None) -> dict[str, Any]:
        self.maybe_sample(db, settings)
        instant = self._build_point(db, settings)
        return {
            "sampled_at": time.time(),
            "interval_s": self.interval_s,
            "instant": instant,
            "series": self._series.snapshot(),
            "http": self.http.snapshot(),
        }

    def _build_point(self, db: Session | None, settings: Settings | None) -> dict[str, Any]:
        root = self._proc_root()
        api_pid = os.getpid()
        roles = resolve_role_pids(root, api_pid)
        jiffies = _read_jiffies(root)
        cpu_count = os.cpu_count() or 1
        processes: dict[str, dict[str, Any]] = {}
        for role, pid in roles.items():
            prev = self._proc_prev.get(role, _ProcState())
            metrics, times = sample_process(
                root,
                pid,
                prev_times=prev.times,
                prev_jiffies=prev.jiffies,
                cur_jiffies=jiffies,
                cpu_count=cpu_count,
            )
            if times is not None and jiffies is not None:
                self._proc_prev[role] = _ProcState(times=times, jiffies=jiffies)
            processes[role] = _process_dict(metrics)
        aria2 = {**_aria2_global(db, settings), "notify_mode": _aria2_notify_mode(db)}
        wakes = _wake_counts(db)
        worker = _worker_runtime(db)
        return {
            "processes": processes,
            "aria2": aria2,
            "worker_wakes": wakes,
            "task_durations": worker.get("task_durations") or {},
            "source_requests": worker.get("source_requests") or {},
        }

    def lazy_tick(self) -> None:
        """Called after HTTP traffic; samples only when the interval elapsed."""
        self.maybe_sample(None, None)


def _process_dict(metrics: ProcessMetrics) -> dict[str, Any]:
    return {
        "pid": metrics.pid,
        "cpu_percent": metrics.cpu_percent,
        "rss_bytes": metrics.rss_bytes,
        "write_bytes": metrics.write_bytes,
    }


def _read_jiffies(root) -> int | None:
    from mediatracker.core.metrics.proc import read_system_jiffies

    return read_system_jiffies(root)


def _aria2_notify_mode(db: Session | None) -> str | None:
    if db is None:
        return None
    from mediatracker.core.aria2_events import read_notify_mode

    return read_notify_mode(db)


def _aria2_global(db: Session | None, settings: Settings | None) -> dict[str, int | None]:
    empty = {
        "download_speed": None,
        "upload_speed": None,
        "num_active": None,
        "num_waiting": None,
    }
    if db is None or settings is None:
        return empty
    row = db.scalar(select(Provider).where(Provider.kind == "downloader", Provider.type == "aria2"))
    if row is None:
        return empty
    client: Aria2Client | None = None
    try:
        from mediatracker.health_report import _instantiate

        downloader = _instantiate(row, settings)
        client = downloader.client  # type: ignore[attr-defined]
        stat = client.global_stat()
    except Exception:
        return empty
    finally:
        if client is not None:
            client.close()
    if not isinstance(stat, dict):
        return empty
    return {
        "download_speed": _int_or_none(stat.get("downloadSpeed")),
        "upload_speed": _int_or_none(stat.get("uploadSpeed")),
        "num_active": _int_or_none(stat.get("numActive")),
        "num_waiting": _int_or_none(stat.get("numWaiting")),
    }


def _wake_counts(db: Session | None) -> dict[str, int]:
    if db is None:
        return {}
    from mediatracker.core.worker_wake import wake_counts

    return wake_counts(db)


def _worker_runtime(db: Session | None) -> dict[str, Any]:
    if db is None:
        return {}
    from mediatracker.core.metrics.worker_counters import public_snapshot

    return public_snapshot(db)


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None

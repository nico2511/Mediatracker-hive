"""Filet du nombre de téléchargements grant actifs servis en parallèle (hive_transfer_parallel)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class TransferParallelExceeded(Exception):
    """Trop de transferts grant simultanés sur cette instance source."""


STALE_PARALLEL_SLOT_SECONDS = 120.0


@dataclass
class TransferParallelGate:
    """Un slot logique par grant_id ; compteur pour les connexions Range aria2 parallèles."""

    _refcounts: dict[int, int] = field(default_factory=dict)
    _grant_bytes: dict[int, int] = field(default_factory=dict)
    _grant_last_activity: dict[int, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _clock: callable = time.monotonic

    def active_count(self) -> int:
        with self._lock:
            return len(self._refcounts)

    def is_grant_active(self, grant_id: int) -> bool:
        with self._lock:
            return self._refcounts.get(grant_id, 0) > 0

    def grant_bytes_sent(self, grant_id: int) -> int:
        with self._lock:
            return int(self._grant_bytes.get(grant_id, 0))

    def grant_inflight_count(self, grant_id: int) -> int:
        with self._lock:
            return int(self._refcounts.get(grant_id, 0))

    def record_grant_bytes(self, grant_id: int, nbytes: int) -> None:
        if nbytes <= 0:
            return
        with self._lock:
            self._grant_bytes[grant_id] = int(self._grant_bytes.get(grant_id, 0)) + nbytes
            self._touch_activity_locked(grant_id)

    def _touch_activity_locked(self, grant_id: int) -> None:
        self._grant_last_activity[grant_id] = self._clock()

    def _purge_stale_slots_locked(self, *, max_idle_seconds: float = STALE_PARALLEL_SLOT_SECONDS) -> None:
        """Libère les slots sans activité depuis trop longtemps (client déconnecté avant le flux)."""
        moment = self._clock()
        stale = [
            grant_id
            for grant_id in list(self._refcounts)
            if moment - self._grant_last_activity.get(grant_id, moment) > max_idle_seconds
        ]
        for grant_id in stale:
            self._refcounts.pop(grant_id, None)
            self._grant_bytes.pop(grant_id, None)
            self._grant_last_activity.pop(grant_id, None)

    def transfer_pct_for_grant(self, grant_id: int, content_size: int) -> float | None:
        if content_size <= 0:
            return None
        with self._lock:
            sent = int(self._grant_bytes.get(grant_id, 0))
            active = self._refcounts.get(grant_id, 0) > 0
        if sent <= 0 and not active:
            return None
        return min(100.0, round(100.0 * sent / content_size, 1))

    def acquire(self, grant_id: int, limit: int) -> bool:
        with self._lock:
            self._purge_stale_slots_locked()
            if self._refcounts.get(grant_id, 0) > 0:
                self._refcounts[grant_id] += 1
                self._touch_activity_locked(grant_id)
                return True
            if len(self._refcounts) >= max(1, limit):
                return False
            self._refcounts[grant_id] = 1
            self._touch_activity_locked(grant_id)
            return True

    def release(self, grant_id: int) -> None:
        with self._lock:
            count = self._refcounts.get(grant_id, 0)
            if count <= 1:
                self._refcounts.pop(grant_id, None)
                self._grant_bytes.pop(grant_id, None)
                self._grant_last_activity.pop(grant_id, None)
            else:
                self._refcounts[grant_id] = count - 1
                self._touch_activity_locked(grant_id)

    def reset(self) -> None:
        with self._lock:
            self._refcounts.clear()
            self._grant_bytes.clear()
            self._grant_last_activity.clear()

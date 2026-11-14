"""Débit d'envoi/réception par clé Hive-Id (seau à jetons, T5)."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


class TransferBandwidthDenied(Exception):
    """Débit dépassé → 429."""


@dataclass
class TransferBandwidthLimiter:
    """Seau par Hive-Id ; bps=0 signifie illimité."""

    bps: float = 0.0
    _balance: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _updated: dict[str, float] = field(default_factory=dict)
    _clock: callable = time.monotonic

    def set_clock(self, fn: callable) -> None:
        self._clock = fn

    def set_bps(self, bps: float) -> None:
        self.bps = max(0.0, float(bps))

    def _refill(self, hive_id: str, moment: float) -> None:
        if self.bps <= 0:
            self._balance[hive_id] = float("inf")
            return
        if hive_id not in self._updated:
            self._balance[hive_id] = self.bps * 2
            self._updated[hive_id] = moment
            return
        last = self._updated[hive_id]
        elapsed = max(0.0, moment - last)
        self._balance[hive_id] = min(self.bps * 2, self._balance.get(hive_id, 0.0) + elapsed * self.bps)
        self._updated[hive_id] = moment

    def reserve(self, hive_id: str, nbytes: int) -> None:
        if nbytes <= 0:
            return
        moment = self._clock()
        self._refill(hive_id, moment)
        bal = self._balance.get(hive_id, 0.0)
        if bal < nbytes:
            raise TransferBandwidthDenied()
        if bal != float("inf"):
            self._balance[hive_id] = bal - nbytes

    def reset(self) -> None:
        self._balance.clear()
        self._updated.clear()

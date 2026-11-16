"""Débit d'envoi/réception par clé Hive-Id (seau à jetons, T5)."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


class TransferBandwidthDenied(Exception):
    """Débit dépassé → 429."""


_OUTBOUND_RATE_WINDOW_SEC = 3.0


@dataclass
class TransferBandwidthLimiter:
    """Seau par Hive-Id ; bps=0 signifie illimité."""

    bps: float = 0.0
    _balance: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    _updated: dict[str, float] = field(default_factory=dict)
    _send_log: list[tuple[float, int]] = field(default_factory=list)
    _clock: callable = time.monotonic

    def set_clock(self, fn: callable) -> None:
        self._clock = fn

    def set_bps(self, bps: float) -> None:
        self.bps = max(0.0, float(bps))

    def _prune_send_log(self, moment: float) -> None:
        cutoff = moment - _OUTBOUND_RATE_WINDOW_SEC
        while self._send_log and self._send_log[0][0] < cutoff:
            self._send_log.pop(0)

    def estimated_outbound_bps(self) -> float | None:
        """Débit sortant mesuré sur la fenêtre glissante ; 0 si inactif mais limité."""
        if self.bps <= 0:
            return None
        moment = self._clock()
        self._prune_send_log(moment)
        if not self._send_log:
            return 0.0
        oldest = self._send_log[0][0]
        span = max(0.001, moment - oldest)
        total = sum(nbytes for _, nbytes in self._send_log)
        return float(total) / span

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
        if self.bps > 0:
            self._prune_send_log(moment)
            self._send_log.append((moment, nbytes))
        self._refill(hive_id, moment)
        bal = self._balance.get(hive_id, 0.0)
        if bal < nbytes:
            raise TransferBandwidthDenied()
        if bal != float("inf"):
            self._balance[hive_id] = bal - nbytes

    def reset(self) -> None:
        self._balance.clear()
        self._updated.clear()
        self._send_log.clear()

"""Filet du nombre de téléchargements grant actifs servis en parallèle (hive_transfer_parallel)."""

from __future__ import annotations

from dataclasses import dataclass, field


class TransferParallelExceeded(Exception):
    """Trop de transferts grant simultanés sur cette instance source."""


@dataclass
class TransferParallelGate:
    """Un slot logique par grant_id ; compteur pour les connexions Range aria2 parallèles."""

    _refcounts: dict[int, int] = field(default_factory=dict)

    def active_count(self) -> int:
        return len(self._refcounts)

    def acquire(self, grant_id: int, limit: int) -> bool:
        if self._refcounts.get(grant_id, 0) > 0:
            self._refcounts[grant_id] += 1
            return True
        if len(self._refcounts) >= max(1, limit):
            return False
        self._refcounts[grant_id] = 1
        return True

    def release(self, grant_id: int) -> None:
        count = self._refcounts.get(grant_id, 0)
        if count <= 1:
            self._refcounts.pop(grant_id, None)
        else:
            self._refcounts[grant_id] = count - 1

    def reset(self) -> None:
        self._refcounts.clear()

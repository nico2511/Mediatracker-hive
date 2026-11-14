"""Débit des requêtes pair : un seau par Hive-Id (jamais par IP)."""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field


class PeerRateLimited(Exception):
    """Débit pair dépassé → 429."""


@dataclass
class PeerRateLimiter:
    limit: int = 30
    window_seconds: float = 60.0
    _hits: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))

    def check(self, hive_id: str, *, now: float | None = None) -> None:
        moment = now if now is not None else time.monotonic()
        window = self.window_seconds
        hits = self._hits[hive_id]
        cutoff = moment - window
        hits[:] = [t for t in hits if t > cutoff]
        if len(hits) >= self.limit:
            raise PeerRateLimited()
        hits.append(moment)

    def reset(self) -> None:
        self._hits.clear()

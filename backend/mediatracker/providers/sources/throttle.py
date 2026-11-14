"""Global pause between source HTTP calls, and the delay after an error.

Any source module can use this. It does not know which site it is protecting.
"""

import random
import threading
import time
from collections.abc import Callable
from datetime import timedelta


class SourcePacer:
    """At most one request every ``delay`` seconds, plus a little jitter.

    The first call does not wait. Later calls wait out the remaining gap.
    A lock keeps two callers (the worker and a manual check) from firing together.
    """

    def __init__(
        self,
        delay: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.delay = max(0.0, delay)
        self._clock = clock
        self._sleeper = sleeper
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._next = 0.0
        self.waits: list[float] = []

    def wait(self) -> float:
        with self._lock:
            now = self._clock()
            gap = max(0.0, self._next - now)
            spread = self.delay * 0.2 if self.delay else 0.0
            jitter = self._rng.uniform(0, spread) if spread else 0.0
            self._next = now + gap + self.delay + jitter
            self.waits.append(gap)
        if gap:
            self._sleeper(gap)
        return gap


class HtmlCache:
    """Short memory of a fetched page. ``ttl`` of 0 disables it."""

    def __init__(self, ttl: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl = ttl
        self._clock = clock
        self._items: dict[str, tuple[float, str]] = {}
        self._bypass = False

    def begin_bypass(self) -> None:
        """The next reads miss. The fresh pages are stored again afterwards."""
        self._bypass = True

    def end_bypass(self) -> None:
        self._bypass = False

    def get(self, url: str) -> str | None:
        if self._bypass or self.ttl <= 0:
            return None
        item = self._items.get(url)
        if item is None:
            return None
        expires, body = item
        if self._clock() >= expires:
            self._items.pop(url, None)
            return None
        return body

    def set(self, url: str, body: str) -> None:
        if self.ttl <= 0:
            return
        self._items[url] = (self._clock() + self.ttl, body)


def backoff_delay(streak: int, base_minutes: int, *, cap_minutes: int = 1440) -> timedelta:
    """Exponential delay. Streak 1 is the base, then it doubles, and stops at the cap."""
    power = min(max(streak, 1) - 1, 8)
    minutes = min(base_minutes * (2**power), cap_minutes)
    return timedelta(minutes=minutes)

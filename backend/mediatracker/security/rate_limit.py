"""In-memory login rate limit. One API process, so a process-local window is enough."""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable


class LoginRateLimiter:
    def __init__(
        self,
        limit: int = 5,
        window_seconds: int = 60,
        lockout_seconds: int = 900,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.limit = limit
        self.window = window_seconds
        self.lockout = lockout_seconds
        self._clock = clock or time.monotonic
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def too_many(self, key: str) -> bool:
        now = self._clock()
        with self._lock:
            return self._blocked(key, now)

    def retry_after(self, key: str) -> int:
        now = self._clock()
        with self._lock:
            until = self._locked_until.get(key)
            if until is not None and until > now:
                remaining = until - now
                whole = int(remaining)
                if remaining > whole:
                    whole += 1
                return max(1, whole)
            return max(1, self.window)

    def record_failure(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            if self._blocked(key, now):
                return
            self._prune(key, now)
            self._failures[key].append(now)
            if len(self._failures[key]) >= self.limit:
                self._locked_until[key] = now + self.lockout

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._failures.clear()
                self._locked_until.clear()
            else:
                self._failures.pop(key, None)
                self._locked_until.pop(key, None)

    def _blocked(self, key: str, now: float) -> bool:
        until = self._locked_until.get(key)
        if until is not None:
            if now < until:
                return True
            self._locked_until.pop(key, None)
            self._failures.pop(key, None)
            return False
        self._prune(key, now)
        return len(self._failures[key]) >= self.limit

    def _prune(self, key: str, now: float) -> None:
        queue = self._failures[key]
        while queue and now - queue[0] > self.window:
            queue.popleft()

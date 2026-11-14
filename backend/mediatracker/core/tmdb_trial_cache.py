"""Short-lived in-process cache for TMDB trial browse calls (no SQLite)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

_TTL_SECONDS = 6 * 3600.0
T = TypeVar("T")

_lock = threading.Lock()
_entries: dict[str, tuple[float, Any]] = {}


def cached_tmdb_trial[T](key: str, loader: Callable[[], T], *, ttl: float = _TTL_SECONDS) -> T:
    """Return a cached value or call ``loader`` once per TTL window."""
    now = time.monotonic()
    with _lock:
        row = _entries.get(key)
        if row is not None and now - row[0] < ttl:
            return row[1]  # type: ignore[return-value]
    value = loader()
    with _lock:
        _entries[key] = (now, value)
    return value


def cache_key(prefix: str, params: dict[str, Any]) -> str:
    return f"{prefix}:{json.dumps(params, sort_keys=True, separators=(',', ':'))}"


def clear_tmdb_trial_cache() -> None:
    with _lock:
        _entries.clear()

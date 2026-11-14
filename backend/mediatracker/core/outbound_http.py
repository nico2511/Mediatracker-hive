"""Process-wide httpx.Client reuse for outbound integrations.

Each pool entry is keyed by a stable tuple (service, host, …). Tests pass a custom
``transport`` or replace ``httpx.Client``; those clients are owned by the caller.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import httpx

_LOCK = threading.Lock()
_POOL: dict[tuple[Any, ...], httpx.Client] = {}


def borrow_http_client(
    key: tuple[Any, ...],
    factory: Callable[..., httpx.Client],
    *,
    transport: httpx.BaseTransport | None = None,
    **kwargs: Any,
) -> tuple[httpx.Client, bool]:
    """Return ``(client, owned)``. Shared clients must not be closed by the caller."""
    if transport is not None or factory is not httpx.Client:
        return factory(transport=transport, **kwargs), True
    with _LOCK:
        current = _POOL.get(key)
        if current is not None and not current.is_closed:
            return current, False
        client = factory(**kwargs)
        previous = _POOL.get(key)
        if previous is not None and previous is not client and not previous.is_closed:
            previous.close()
        _POOL[key] = client
        return client, False


def close_outbound_http_clients() -> None:
    """Close pooled httpx clients. Called on API and worker shutdown."""
    with _LOCK:
        for client in _POOL.values():
            if not client.is_closed:
                client.close()
        _POOL.clear()


def shutdown_outbound_integrations() -> None:
    """Close pooled HTTP sessions and integration wrappers that own them."""
    from mediatracker.integrations.tmdb import close_shared_tmdb

    close_shared_tmdb()
    close_outbound_http_clients()

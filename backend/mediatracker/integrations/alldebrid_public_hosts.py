"""Liste publique des hébergeurs AllDebrid (/v4/hosts), mise en cache côté serveur."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from typing import Any

import httpx

from mediatracker.core.selection import (
    BLOCKED_SOURCE_HOSTERS,
    DEFAULT_HOSTER_PREFERENCE,
    HOSTER_LAST,
    KNOWN_HOSTERS,
)
from mediatracker.providers.debrid.alldebrid import AGENT

logger = logging.getLogger(__name__)

_PUBLIC_HOSTS_URL = "https://api.alldebrid.com/v4/hosts"
_CACHE_TTL_SECONDS = 4 * 3600.0
_lock = threading.Lock()
_cached_at = 0.0
_cached_names: tuple[str, ...] | None = None

_STATIC_FALLBACK: tuple[str, ...] = tuple(
    sorted({name for name in KNOWN_HOSTERS if name not in BLOCKED_SOURCE_HOSTERS}, key=str.casefold)
)
_NON_HOSTER_API_KEYS = frozenset({"alldebrid", "example"})


def _normalize_host_key(raw: str) -> str:
    return (raw or "").strip().lower().replace(" ", "")


def _order_hoster_names(names: Iterable[str]) -> list[str]:
    ordered = sorted({name for name in names if name and name not in BLOCKED_SOURCE_HOSTERS}, key=str.casefold)
    if HOSTER_LAST in ordered:
        head = [item for item in ordered if item != HOSTER_LAST]
        return head + [HOSTER_LAST]
    if HOSTER_LAST in KNOWN_HOSTERS:
        return [*ordered, HOSTER_LAST]
    return ordered


def _merge_with_known_hosters(api_names: Iterable[str]) -> list[str]:
    merged = {name for name in api_names if name not in BLOCKED_SOURCE_HOSTERS}
    merged |= {name for name in KNOWN_HOSTERS if name not in BLOCKED_SOURCE_HOSTERS}
    return _order_hoster_names(merged)


def _parse_public_hosts(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    hosts = (data or {}).get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, dict):
        return []
    names: list[str] = []
    for key in hosts:
        name = _normalize_host_key(str(key))
        if not name or name in BLOCKED_SOURCE_HOSTERS or name in _NON_HOSTER_API_KEYS:
            continue
        if name not in names:
            names.append(name)
    return _merge_with_known_hosters(names)


def _fetch_public_hosts() -> list[str] | None:
    try:
        response = httpx.get(
            _PUBLIC_HOSTS_URL,
            params={"hostsOnly": "true"},
            headers={"User-Agent": AGENT},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.info("Liste publique AllDebrid indisponible : %s", exc)
        return None
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return None
    parsed = _parse_public_hosts(payload)
    return parsed or None


def allowed_hoster_names(*, force: bool = False) -> frozenset[str]:
    global _cached_at, _cached_names
    now = time.monotonic()
    with _lock:
        if not force and _cached_names is not None and now - _cached_at < _CACHE_TTL_SECONDS:
            return frozenset(_cached_names)
        fetched = _fetch_public_hosts()
        if fetched:
            merged = _merge_with_known_hosters(fetched)
            _cached_names = tuple(merged)
            _cached_at = now
            return frozenset(_cached_names)
        fallback = _merge_with_known_hosters(_STATIC_FALLBACK or DEFAULT_HOSTER_PREFERENCE)
        _cached_names = tuple(fallback)
        _cached_at = now
        return frozenset(_cached_names)


def list_hoster_picker_options(*, force: bool = False) -> list[str]:
    names = sorted(allowed_hoster_names(force=force), key=str.casefold)
    if HOSTER_LAST in names:
        head = [item for item in names if item != HOSTER_LAST]
        return head + [HOSTER_LAST]
    return names
"""Drop secrets and debrid URLs before a payload leaves the process."""

from typing import Any

from mediatracker.core.hosters import SCRUB_URL_HOSTER_MARKERS
from mediatracker.providers.downloaders.aria2 import scrub_url

_DROP_KEYS = frozenset(
    {
        "secret",
        "token",
        "password",
        "api_key",
        "authorization",
        "direct_url",
        "direct",
        "link_url",
        "cdn",
        "cdn_url",
    }
)
_SAFE_URL_KEYS = frozenset({"poster_url", "page_url", "url", "backdrop_url"})


def scrub_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _DROP_KEYS:
            continue
        clean[key] = _scrub_value(key, value)
    return clean


def _scrub_value(key: str, value: Any) -> Any:
    if isinstance(value, dict):
        return scrub_payload(value)
    if isinstance(value, list):
        return [_scrub_value(key, item) for item in value]
    if isinstance(value, str):
        if key in _SAFE_URL_KEYS and _public_url(value):
            return value
        return scrub_url(value)
    return value


def _public_url(value: str) -> bool:
    if not (value.startswith("http://") or value.startswith("https://")):
        return False
    lowered = value.lower()
    return not any(host in lowered for host in SCRUB_URL_HOSTER_MARKERS)

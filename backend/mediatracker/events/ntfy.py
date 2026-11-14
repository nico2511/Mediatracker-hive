"""ntfy message shape. Delivery itself is the webhook worker."""

from dataclasses import dataclass
from typing import Any

from mediatracker.events.scrub import scrub_payload
from mediatracker.events.text import notification_text

_HIGH = frozenset({"download.failed", "debrid.auth_blocked", "source.error"})
_TAGS = {
    "item.available": "tv,bell",
    "download.failed": "warning,x",
    "debrid.auth_blocked": "warning",
    "source.error": "warning,cloud",
    "season.announced": "tv",
    "cleanup.upcoming": "wastebasket",
    "cleanup.done": "wastebasket,white_check_mark",
    "cleanup.pass": "wastebasket",
    "cleanup.failed": "warning,wastebasket",
    "trash.purged": "wastebasket",
    "trash.emptied": "wastebasket",
    "notify.test": "test_tube",
    "system.updated": "arrow_up,white_check_mark",
    "nouveautes": "new",
}


@dataclass(frozen=True)
class NtfyMessage:
    title: str
    message: str
    tags: str
    priority: str

    def headers(self, token: str | None = None) -> dict[str, str]:
        headers = {
            "Title": self.title,
            "Tags": self.tags,
            "Priority": self.priority,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers


def format_ntfy(event_type: str, payload: dict[str, Any]) -> NtfyMessage:
    safe = scrub_payload(payload)
    title, message = notification_text(event_type, safe)
    season = safe.get("season")
    episode = safe.get("episode")
    if event_type == "library.imported" and isinstance(season, int) and isinstance(episode, int):
        tags = "tv,white_check_mark"
    elif event_type == "library.imported":
        tags = "movie,white_check_mark"
    else:
        tags = _TAGS.get(event_type, "info")
    priority = "high" if event_type in _HIGH else "default"
    return NtfyMessage(title=title, message=message, tags=tags, priority=priority)

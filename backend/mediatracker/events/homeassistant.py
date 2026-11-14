"""Home Assistant webhook body.

Delivery (POST JSON, no HMAC) is the webhook worker. The URL stored on the
webhook is ``http://<hôte>:8123/api/webhook/<id>``; the id is the credential.

``episode`` stays an object (``id``, ``season``, ``number``, ``title``, ``path``)
so an automation can keep reading ``trigger.json.episode.number``. The episode
number is also ``episode_number``. Flat ids (``media_id``, ``tmdb_id``,
``season``, ``url``) are there for a later dashboard.
"""

from typing import Any

from mediatracker.events.scrub import scrub_payload
from mediatracker.events.text import notification_text


def format_homeassistant(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """JSON body posted to a Home Assistant webhook trigger."""
    safe = scrub_payload(payload)
    title, message = notification_text(event_type, safe)
    season = safe.get("season")
    number = safe.get("episode")
    if not isinstance(number, int):
        number = safe.get("episode_number") if isinstance(safe.get("episode_number"), int) else None
    episode: dict[str, Any] | None = None
    if safe.get("item_id") is not None or isinstance(season, int) or isinstance(number, int):
        episode = {
            "id": safe.get("item_id"),
            "season": season if isinstance(season, int) else None,
            "number": number if isinstance(number, int) else None,
            "title": safe.get("episode_title"),
            "path": safe.get("path"),
        }
    media: dict[str, Any] | None = None
    if any(safe.get(key) is not None for key in ("media_id", "title", "media_type", "tmdb_id")):
        media = {
            "id": safe.get("media_id"),
            "title": safe.get("title"),
            "type": safe.get("media_type"),
            "tmdb_id": safe.get("tmdb_id"),
        }
    poster = safe.get("poster_url")
    page = safe.get("page_url") if isinstance(safe.get("page_url"), str) else None
    items = safe.get("items") if isinstance(safe.get("items"), list) else None
    body: dict[str, Any] = {
        "event": event_type,
        "title": title,
        "message": message,
        "poster_url": poster if isinstance(poster, str) and poster else None,
        "media_id": safe.get("media_id"),
        "tmdb_id": safe.get("tmdb_id"),
        "item_id": safe.get("item_id"),
        "season": season if isinstance(season, int) else None,
        "episode_number": number if isinstance(number, int) else None,
        "url": page or None,
        "level": safe.get("level") or "info",
        "media": media,
        "episode": episode,
    }
    if items:
        body["grouped"] = True
        body["count"] = safe.get("count") if isinstance(safe.get("count"), int) else len(items)
        body["items"] = items
    if page:
        body["actions"] = [{"id": "open", "title": "Ouvrir", "url": page}]
    # Home Assistant replaces a phone notification that reuses this tag.
    code = safe.get("error_code") if isinstance(safe.get("error_code"), str) else ""
    body["tag"] = f"mediatracker-{event_type}-{safe.get('media_id')}-{safe.get('item_id')}-{code}"
    return body

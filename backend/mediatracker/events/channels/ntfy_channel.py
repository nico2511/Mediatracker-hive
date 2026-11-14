"""ntfy notification channel."""

from typing import Any

from mediatracker.db.models import Webhook
from mediatracker.events.ntfy import format_ntfy
from mediatracker.events.scrub import scrub_payload
from mediatracker.security.crypto import decrypt_secret


class NtfyChannel:
    name = "ntfy"

    def is_enabled(self, hook: Webhook) -> bool:
        return bool(hook.enabled)

    def render(
        self,
        hook: Webhook,
        event_type: str,
        payload: dict[str, Any],
        secret_key: str,
    ) -> tuple[bytes, dict[str, str]]:
        safe = scrub_payload(payload)
        message = format_ntfy(event_type, safe)
        token = decrypt_secret(hook.secret, secret_key) if hook.secret else ""
        headers = message.headers(token or None)
        page = safe.get("page_url")
        if isinstance(page, str) and page:
            headers["Click"] = page
        poster = safe.get("poster_url")
        if isinstance(poster, str) and poster.startswith("https://image.tmdb.org/"):
            headers["Attach"] = poster
        return message.message.encode("utf-8"), {**headers, "Content-Type": "text/plain; charset=utf-8"}

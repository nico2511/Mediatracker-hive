"""Home Assistant notification channel."""

import json
from typing import Any

from mediatracker.db.models import Webhook
from mediatracker.events.homeassistant import format_homeassistant
from mediatracker.events.scrub import scrub_payload


class HomeAssistantChannel:
    name = "homeassistant"

    def is_enabled(self, hook: Webhook) -> bool:
        return bool(hook.enabled)

    def render(
        self,
        hook: Webhook,
        event_type: str,
        payload: dict[str, Any],
        secret_key: str,
    ) -> tuple[bytes, dict[str, str]]:
        del secret_key
        safe = scrub_payload(payload)
        rendered = format_homeassistant(event_type, safe)
        raw = json.dumps(rendered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return raw, {"Content-Type": "application/json"}

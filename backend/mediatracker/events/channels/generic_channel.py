"""Signed generic webhook notification channel."""

import json
from datetime import datetime, timezone
from typing import Any

from mediatracker.db.models import Webhook
from mediatracker.events.homeassistant import format_homeassistant
from mediatracker.events.scrub import scrub_payload
from mediatracker.events.signing import signature_headers
from mediatracker.security.crypto import decrypt_secret


class GenericChannel:
    name = "generic"

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
        rendered = format_homeassistant(event_type, safe)
        raw = json.dumps(rendered, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers: dict[str, str] = {"Content-Type": "application/json"}
        secret = decrypt_secret(hook.secret, secret_key) if hook.secret else ""
        stamp = str(int(datetime.now(timezone.utc).timestamp()))
        headers.update(signature_headers(secret, stamp, raw))
        return raw, headers

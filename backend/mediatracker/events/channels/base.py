"""Common notification channel interface."""

from typing import Any, Protocol

from mediatracker.db.models import Webhook


class NotificationChannel(Protocol):
    """One outbound notification target kind (ntfy, Home Assistant, signed webhook)."""

    @property
    def name(self) -> str:
        """Webhook ``type`` value this channel handles."""

    def is_enabled(self, hook: Webhook) -> bool:
        """Whether deliveries should run for this hook given current settings."""

    def render(
        self,
        hook: Webhook,
        event_type: str,
        payload: dict[str, Any],
        secret_key: str,
    ) -> tuple[bytes, dict[str, str]]:
        """HTTP body and headers for one POST. Payload is already scrubbed upstream."""

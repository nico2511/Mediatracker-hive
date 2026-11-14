"""Registry of notification channels keyed by webhook type."""

import logging

from mediatracker.db.models import Webhook
from mediatracker.events.channels.base import NotificationChannel

logger = logging.getLogger(__name__)

_by_name: dict[str, NotificationChannel] = {}
_FALLBACK_TYPE = "homeassistant"


def register(channel: NotificationChannel) -> None:
    _by_name[channel.name] = channel


def get(name: str) -> NotificationChannel:
    try:
        return _by_name[name]
    except KeyError:
        raise KeyError(f"unknown notification channel: {name}") from None


def for_hook(hook: Webhook) -> NotificationChannel:
    channel = _by_name.get(hook.type)
    if channel is not None:
        return channel
    logger.debug("Type de webhook inconnu %r : rendu Home Assistant.", hook.type)
    return get(_FALLBACK_TYPE)


def all_channels() -> tuple[NotificationChannel, ...]:
    return tuple(_by_name.values())


def known_types() -> frozenset[str]:
    return frozenset(_by_name)

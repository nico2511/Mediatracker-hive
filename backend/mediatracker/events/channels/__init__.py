"""Notification channel registry."""

from mediatracker.events.channels.generic_channel import GenericChannel
from mediatracker.events.channels.homeassistant_channel import HomeAssistantChannel
from mediatracker.events.channels.ntfy_channel import NtfyChannel
from mediatracker.events.channels.registry import all_channels, for_hook, get, known_types, register

register(NtfyChannel())
register(HomeAssistantChannel())
register(GenericChannel())

__all__ = ["all_channels", "for_hook", "get", "known_types", "register"]

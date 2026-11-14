"""Notification channel registry and pluggable renderers."""

from typing import Any

from mediatracker.db.models import Webhook
from mediatracker.events.channels import all_channels, for_hook, get, known_types, register
from mediatracker.events.channels.registry import _by_name


def test_for_hook_unknown_type_falls_back_to_homeassistant(caplog):
    import logging

    hook = Webhook(name="legacy", url="https://example.invalid/wh", type="unknown-kind", enabled=True)
    with caplog.at_level(logging.DEBUG, logger="mediatracker.events.channels.registry"):
        channel = for_hook(hook)
    assert channel.name == "homeassistant"
    assert "unknown-kind" in caplog.text


def test_registry_lists_builtin_channels():
    names = {channel.name for channel in all_channels()}
    assert names == {"ntfy", "homeassistant", "generic"}
    assert known_types() == names
    assert get("ntfy").name == "ntfy"
    hook = Webhook(name="x", url="https://ntfy.example/topic", type="generic", enabled=True)
    assert for_hook(hook).name == "generic"


def test_fake_channel_registers_and_renders():
    class FakeChannel:
        name = "fake-test"

        def is_enabled(self, hook: Webhook) -> bool:
            return bool(hook.enabled)

        def render(
            self,
            hook: Webhook,
            event_type: str,
            payload: dict[str, Any],
            secret_key: str,
        ) -> tuple[bytes, dict[str, str]]:
            del hook, secret_key
            body = f"{event_type}:{payload.get('title', '')}".encode()
            return body, {"Content-Type": "text/plain", "X-Test": "1"}

    prior = dict(_by_name)
    try:
        register(FakeChannel())
        channel = get("fake-test")
        hook = Webhook(name="probe", url="https://example.invalid/hook", type="fake-test", enabled=True)
        assert for_hook(hook) is channel
        assert channel.is_enabled(hook) is True
        hook.enabled = False
        assert channel.is_enabled(hook) is False
        body, headers = channel.render(hook, "notify.test", {"title": "Probe"}, "key")
        assert body == b"notify.test:Probe"
        assert headers["X-Test"] == "1"
    finally:
        _by_name.clear()
        _by_name.update(prior)

"""Catalog source registry (W9a): pluggable poll sources with injected DB session."""

import logging

from mediatracker.providers.sources.registry import _by_name, for_type, get, known_types, register


def test_builtin_wawacity_is_registered():
    assert "wawacity" in known_types()
    assert get("wawacity").name == "wawacity"


def test_for_type_unknown_logs_and_returns_none(caplog):
    with caplog.at_level(logging.DEBUG, logger="mediatracker.providers.sources.registry"):
        assert for_type("no-such-catalog-source") is None
    assert "no-such-catalog-source" in caplog.text


def test_fake_source_registers_and_opens_with_injected_db():
    seen_db: list[object] = []

    class _FakeClient:
        capabilities = frozenset({"links"})

        def close(self) -> None:
            pass

    class FakeCatalogSource:
        name = "fake-catalog-test"
        capabilities = frozenset({"links"})

        def provider_id(self, db) -> int:
            return 99

        def binding_page_url(self, db, settings, binding) -> str | None:
            del settings
            return (binding.url or "").strip() or None

        def open_poll(self, db, settings, **kwargs) -> object | None:
            del settings, kwargs
            seen_db.append(db)
            return _FakeClient()

        def open_cached(self, db, settings, **kwargs) -> object | None:
            return self.open_poll(db, settings, **kwargs)

        def probe_binding_page(self, db, settings, page_url, *, transport=None):
            del db, settings, page_url, transport
            from mediatracker.core.source_binding import SourceBindingProbe

            return SourceBindingProbe(True, "Probe", {})

    prior = dict(_by_name)
    try:
        register(FakeCatalogSource())
        assert "fake-catalog-test" in known_types()
        entry = get("fake-catalog-test")
        sentinel_db = object()
        client = entry.open_poll(sentinel_db, object())
        assert client is not None
        assert seen_db == [sentinel_db]
        assert client.capabilities == frozenset({"links"})
    finally:
        _by_name.clear()
        _by_name.update(prior)

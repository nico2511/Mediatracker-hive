"""Probe à liens : ``get_links`` reçoit un ``SourceRef`` (comme le worker)."""

from __future__ import annotations

from mediatracker.core.source_probe import SourceProbeTarget, _probe_links, _stub_item, _stub_media
from mediatracker.providers.base import SourceRef


def test_probe_links_passes_source_ref_to_get_links(client, app, monkeypatch):
    db = app.state.session_factory()
    try:
        seen: list[SourceRef] = []

        def fake_search(_source, _media, _seasons, *_args, **_kwargs):
            row = type("Page", (), {"ref": "ref-demo", "url": "https://catalog.example/item/42"})()
            return [row]

        class FakeSource:
            def get_links(self, ref: SourceRef):
                _ = ref.url
                seen.append(ref)
                return [type("Link", (), {"hoster": "rapidgator"})()]

        monkeypatch.setattr(
            "mediatracker.core.source_probe.remote_links_source",
            lambda _db, _settings, _name: (1, FakeSource()),
        )
        monkeypatch.setattr("mediatracker.core.source_poll._search", fake_search)
        monkeypatch.setattr("mediatracker.core.source_probe.close_source", lambda _source: None)

        target = SourceProbeTarget(
            title="Film Démo",
            media_type="film",
            season=None,
            episode=None,
            imdb_id=None,
        )
        outcome = _probe_links(
            db,
            app.state.settings,
            "wawacity",
            _stub_media(target),
            _stub_item(target),
            preference=["rapidgator"],
            profile=None,
        )
        assert outcome.issue == "found"
        assert len(seen) == 1
        assert seen[0].ref == "ref-demo"
        assert seen[0].url == "https://catalog.example/item/42"
    finally:
        db.close()


def test_probe_links_source_ref_without_page_url(client, app, monkeypatch):
    db = app.state.session_factory()
    try:

        def fake_search(_source, _media, _seasons, *_args, **_kwargs):
            return [type("Page", (), {"ref": "ref-only"})()]

        class FakeSource:
            def get_links(self, ref: SourceRef):
                assert ref.ref == "ref-only"
                assert ref.url is None
                return []

        monkeypatch.setattr(
            "mediatracker.core.source_probe.remote_links_source",
            lambda _db, _settings, _name: (1, FakeSource()),
        )
        monkeypatch.setattr("mediatracker.core.source_poll._search", fake_search)
        monkeypatch.setattr("mediatracker.core.source_probe.close_source", lambda _source: None)

        target = SourceProbeTarget(
            title="Film Démo",
            media_type="film",
            season=None,
            episode=None,
            imdb_id=None,
        )
        outcome = _probe_links(
            db,
            app.state.settings,
            "free_telecharger",
            _stub_media(target),
            _stub_item(target),
            preference=["rapidgator"],
            profile=None,
        )
        assert outcome.issue == "empty"
    finally:
        db.close()

"""Probe sources à liens : repli qualité/langue via réglages en base (pas Settings)."""

from __future__ import annotations

from datetime import datetime, timezone

from mediatracker.core.source_probe import SourceProbeTarget, _probe_links, _stub_item, _stub_media
from mediatracker.db.models import Setting


def test_probe_links_uses_db_default_quality_language(client, app, monkeypatch):
    db = app.state.session_factory()
    try:
        moment = datetime(2026, 11, 12, tzinfo=timezone.utc)
        db.merge(Setting(key="default_quality", value="1080p", is_secret=False, updated_at=moment))
        db.merge(Setting(key="default_language", value="MULTI", is_secret=False, updated_at=moment))
        db.commit()

        captured: dict[str, str] = {}

        def fake_search(_source, _media, _seasons, crit_quality, crit_language, *_args, **_kwargs):
            captured["quality"] = crit_quality
            captured["language"] = crit_language
            return [type("Ref", (), {"ref": "ref-demo"})()]

        class FakeSource:
            def get_links(self, _ref):
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
        media = _stub_media(target)
        item = _stub_item(target)
        preference = ["rapidgator"]

        for source_name in ("wawacity", "free_telecharger"):
            captured.clear()
            outcome = _probe_links(
                db,
                app.state.settings,
                source_name,
                media,
                item,
                preference=preference,
                profile=None,
            )
            assert outcome.issue == "found", source_name
            assert captured["quality"] == "1080p"
            assert captured["language"] == "MULTI"
    finally:
        db.close()

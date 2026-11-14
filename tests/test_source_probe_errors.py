"""Probe manuel des sources : persistance même en cas d'exception."""

from datetime import datetime, timezone

from tests.conftest import login

from mediatracker.core.download_sources_status import download_source_statuses
from mediatracker.core.source_probe import SourceProbeTarget, probe_download_sources


def test_probe_download_sources_records_probe_on_exception(client, app, monkeypatch):
    login(client)
    db = app.state.session_factory()
    try:
        target = SourceProbeTarget(title="Film Démo", media_type="film", season=None, episode=None, imdb_id=None)

        def boom(*_args, **_kwargs):
            raise RuntimeError("échec simulé sans secret")

        monkeypatch.setattr(
            "mediatracker.core.source_probe._effective_probe_order",
            lambda _db, _settings: ["wawacity"],
        )
        monkeypatch.setattr("mediatracker.core.source_probe._source_disabled", lambda _db, _name, _settings: None)
        monkeypatch.setattr("mediatracker.core.source_probe._default_profile", lambda _db: None)
        monkeypatch.setattr(
            "mediatracker.core.source_probe.legacy_comet_context",
            lambda _db, _media, _settings: (None, None),
        )
        monkeypatch.setattr("mediatracker.core.source_probe._probe_one", boom)

        when = datetime(2026, 11, 9, 22, 0, tzinfo=timezone.utc)
        rows = probe_download_sources(db, app.state.settings, target, now=when)
        db.commit()
        assert len(rows) == 1
        assert rows[0].issue == "error"
        bundle = download_source_statuses(db, app.state.settings)
        wawa = next(item for item in bundle.sources if item.source == "wawacity")
        assert wawa.last_probe_issue == "error"
        assert wawa.last_probe_at is not None
        assert wawa.last_probe_link_count == 0
    finally:
        db.close()

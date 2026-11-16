"""Régressions pack ParsedName / isolation d'erreur source (noms fictifs)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy import func, select
from tests.test_comet import PACK_PREFS, PACK_QUERY

from mediatracker.core.pipeline import unlock
from mediatracker.db.models import Event
from mediatracker.providers.sources.comet import select_streams, stream_file_size

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def test_stream_file_size_season_pack_with_file_index_never_uses_torrent_total():
    filename = "Ouvrage.Demo.S01.Complete.1080p.mkv"
    row = {"size": 40 * 1024**3}
    assert stream_file_size(row, {}, filename, file_index=2) is None


def test_stream_file_size_single_episode_with_file_index_uses_row_size():
    filename = "Ouvrage.Demo.S01E05.1080p.mkv"
    size = 600 * 1024 * 1024
    row = {"size": size}
    assert stream_file_size(row, {}, filename, file_index=0) == size


def test_select_streams_pack_with_file_index_populates_sizes():
    payload = {
        "streams": [
            {
                "infoHash": "b" * 40,
                "behaviorHints": {"filename": "Alpha.S01.Complete.1080p.MULTi.mkv"},
            },
            {
                "infoHash": "c" * 40,
                "fileIdx": 2,
                "size": 40 * 1024**3,
                "behaviorHints": {"filename": "Alpha.S01.Complete.1080p.MULTi.mkv"},
            },
            {
                "infoHash": "a" * 40,
                "fileIdx": 0,
                "size": 700 * 1024 * 1024,
                "behaviorHints": {"filename": "Alpha.S01E05.1080p.MULTi.mkv"},
            },
        ]
    }
    chosen = select_streams(payload, PACK_QUERY, PACK_PREFS)
    by_hash = {item.external_id.split(":")[1]: item for item in chosen}
    assert "c" * 40 in by_hash
    assert by_hash["c" * 40].size is None


def test_from_episode_source_swallows_links_for_exception(monkeypatch):
    class _Broken:
        quality_profile = None

        def links_for(self, query, preferences):
            del query, preferences
            raise AttributeError("ParsedName sans pack")

        def close(self) -> None:
            return None

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    row = SimpleNamespace(id=42, poll_quality_profile_id=None, error_code=None, error=None)
    item = SimpleNamespace(id=1, season=1, episode=2)
    media = SimpleNamespace(
        title="Ouvrage Démo",
        original_title=None,
        year=2020,
        type="series",
        id=1,
    )
    settings = SimpleNamespace()
    debrid = SimpleNamespace(upload_magnet=lambda url: url)

    monkeypatch.setattr("mediatracker.providers.sources.loading.episode_source", lambda *a, **k: _Broken())
    monkeypatch.setattr("mediatracker.core.pipeline.unlock.ensure_imdb", lambda *a, **k: "tt0000001")
    monkeypatch.setattr(
        "mediatracker.providers.sources.comet_urls.resolve_comet_base_urls",
        lambda *a, **k: ["https://comet.example/"],
    )
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._commit", lambda _db: None)
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.resolve_download_poll_profile",
        lambda *a, **k: __import__(
            "mediatracker.core.profile_attempt_orchestration", fromlist=["PollProfileResolution"]
        ).PollProfileResolution(profile=None, explicit=False, missing_explicit=False),
    )
    empty_prefs = __import__("mediatracker.providers.base", fromlist=["SourcePreferences"]).SourcePreferences(
        quality="1080p", language="MULTI"
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_adapters.legacy_comet_context",
        lambda *a, **k: (None, empty_prefs),
    )

    assert (
        unlock._from_episode_source(
            db,
            row,
            item,
            media,
            NOW,
            [],
            None,
            debrid,
            settings,
            [],
            source_type="comet",
        )
        is None
    )


def test_second_episode_source_runs_after_first_links_for_raises(monkeypatch):
    """Comme queue._begin : Torrentio en erreur n'empêche pas d'appeler Comet."""
    comet_calls: list[str] = []

    class _FailTorrentio:
        quality_profile = None

        def links_for(self, query, preferences):
            del query, preferences
            raise AttributeError("ParsedName sans pack")

        def close(self) -> None:
            return None

    class _EmptyComet:
        quality_profile = None
        last_empty_reason = None

        def links_for(self, query, preferences):
            del query, preferences
            comet_calls.append("ok")
            return []

        def close(self) -> None:
            return None

    def factory(name, settings, *, db, base_url=None, base_urls=None, transport=None):
        del settings, db, base_url, base_urls, transport
        if name == "torrentio":
            return _FailTorrentio()
        if name == "comet":
            return _EmptyComet()
        return None

    db = MagicMock()
    db.scalars.return_value.all.return_value = []
    row = SimpleNamespace(id=1, poll_quality_profile_id=None, error_code=None, error=None)
    item = SimpleNamespace(id=1, season=1, episode=1)
    media = SimpleNamespace(
        title="Ouvrage Démo",
        original_title=None,
        year=2020,
        type="series",
        id=1,
    )
    settings = SimpleNamespace()
    debrid = SimpleNamespace(upload_magnet=lambda url: url)
    common = dict(
        db=db,
        row=row,
        item=item,
        media=media,
        now=NOW,
        preference=[],
        debrid_row=None,
        debrid=debrid,
        settings=settings,
        known=[],
    )

    monkeypatch.setattr("mediatracker.providers.sources.loading.episode_source", factory)
    monkeypatch.setattr("mediatracker.core.pipeline.unlock.ensure_imdb", lambda *a, **k: "tt0000001")
    monkeypatch.setattr(
        "mediatracker.providers.sources.loading.source_base_url",
        lambda *a, **k: "https://torrentio.example",
    )
    monkeypatch.setattr(
        "mediatracker.providers.sources.comet_urls.resolve_comet_base_urls",
        lambda *a, **k: ["https://comet.example"],
    )
    monkeypatch.setattr("mediatracker.core.pipeline.unlock._commit", lambda _db: None)
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.resolve_download_poll_profile",
        lambda *a, **k: __import__(
            "mediatracker.core.profile_attempt_orchestration", fromlist=["PollProfileResolution"]
        ).PollProfileResolution(profile=None, explicit=False, missing_explicit=False),
    )
    empty_prefs = __import__("mediatracker.providers.base", fromlist=["SourcePreferences"]).SourcePreferences(
        quality="1080p", language="MULTI"
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_adapters.legacy_comet_context",
        lambda *a, **k: (None, empty_prefs),
    )

    for source_type in ("torrentio", "comet"):
        unlock._from_episode_source(**common, source_type=source_type)
    assert comet_calls == ["ok"]


def test_download_source_links_error_journal_deduped(client, app):
    db = app.state.session_factory()
    try:
        row = SimpleNamespace(id=77)
        item = SimpleNamespace(id=None, season=1, episode=2)
        media = SimpleNamespace(id=None, title="Ouvrage Démo")
        err = AttributeError("ParsedName sans pack")
        unlock._record_download_source_links_error(db, row, item, media, "torrentio", err)
        unlock._record_download_source_links_error(db, row, item, media, "torrentio", err)
        db.commit()
        count = db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.type == "download.source_error",
                Event.dedupe_key == "download.source_error:77:torrentio",
            )
        )
        assert count == 1
        event = db.scalar(
            select(Event).where(
                Event.type == "download.source_error",
                Event.dedupe_key == "download.source_error:77:torrentio",
            )
        )
        assert event is not None
        assert event.payload.get("message", "").startswith("Torrentio")
    finally:
        db.close()

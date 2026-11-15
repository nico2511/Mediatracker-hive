"""Filtre taille/durée/qualité avant choix final (noms fictifs)."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from mediatracker.core.download_bitrate_sanity import (
    BITRATE_BOUNDS_BPS,
    DEFAULT_EPISODE_MINUTES,
    DEFAULT_FILM_MINUTES,
    bitrate_sanity_reject_reason,
    episode_count_in_release,
    filter_link_candidates,
    filter_stored_links,
    playback_duration_seconds,
)
from mediatracker.providers.base import LinkCandidate


def _media(*, media_type="film", tmdb_runtime_min=None):
    return SimpleNamespace(type=media_type, tmdb_runtime_min=tmdb_runtime_min)


def _series_item():
    return SimpleNamespace(season=1, episode=3)


def test_too_small_rejected():
    duration = float(DEFAULT_FILM_MINUTES) * 60.0
    tiny = 3 * 1024 * 1024
    reason = bitrate_sanity_reject_reason(size=tiny, quality="1080p", duration_sec=duration)
    assert reason is not None
    assert "trop faible" in reason


def test_too_large_rejected():
    duration = float(DEFAULT_FILM_MINUTES) * 60.0
    huge = 200 * 1024**3
    reason = bitrate_sanity_reject_reason(size=huge, quality="1080p", duration_sec=duration)
    assert reason is not None
    assert "trop élevé" in reason


def test_normal_kept():
    duration = float(DEFAULT_FILM_MINUTES) * 60.0
    size = 4 * 1024**3
    assert bitrate_sanity_reject_reason(size=size, quality="1080p", duration_sec=duration) is None


def test_unknown_size_kept():
    assert bitrate_sanity_reject_reason(size=None, quality="1080p", duration_sec=3600.0) is None


def test_unknown_duration_uses_default_film():
    media = _media(tmdb_runtime_min=None)
    item = SimpleNamespace(season=None, episode=None)
    assert playback_duration_seconds(media, item) == float(DEFAULT_FILM_MINUTES) * 60.0


def test_unknown_duration_uses_default_episode():
    media = _media(media_type="series", tmdb_runtime_min=None)
    sec = playback_duration_seconds(media, _series_item())
    assert sec == float(DEFAULT_EPISODE_MINUTES) * 60.0


def test_multi_episode_duration():
    media = _media(media_type="series", tmdb_runtime_min=50)
    item = _series_item()
    name = "Ouvrage Démo S01E01-E03 1080p.mkv"
    assert episode_count_in_release(name) == 3
    assert playback_duration_seconds(media, item, filename=name) == 50 * 60.0 * 3


def test_filter_link_candidates_drops_only_absurd():
    media = _media(tmdb_runtime_min=110)
    item = SimpleNamespace(season=None, episode=None)
    good = LinkCandidate(
        url="magnet:x",
        kind="magnet",
        quality="1080p",
        size=5 * 1024**3,
        filename="Ouvrage Démo 1080p.mkv",
        external_id="a",
    )
    bad = LinkCandidate(
        url="magnet:y",
        kind="magnet",
        quality="1080p",
        size=3 * 1024 * 1024,
        filename="Ouvrage Démo 1080p sample.mkv",
        external_id="b",
    )
    unknown = LinkCandidate(
        url="magnet:z",
        kind="magnet",
        quality="1080p",
        size=None,
        filename="Ouvrage Démo 1080p.mkv",
        external_id="c",
    )
    out = filter_link_candidates(media, item, [bad, good, unknown])
    assert [c.external_id for c in out] == ["a", "c"]


def test_filter_stored_links_same_rules():
    media = _media(tmdb_runtime_min=110)
    item = SimpleNamespace(season=None, episode=None)
    good = SimpleNamespace(size=5 * 1024**3, quality="1080p", filename="Ouvrage Démo 1080p.mkv")
    bad = SimpleNamespace(size=3 * 1024 * 1024, quality="1080p", filename="Ouvrage Démo 1080p.mkv")
    kept = filter_stored_links(media, item, [bad, good])
    assert kept == [good]


def test_bounds_table_covers_four_buckets():
    assert set(BITRATE_BOUNDS_BPS) == {"480p", "720p", "1080p", "2160p"}


def test_hevc_episode_50mb_kept():
    duration = 22.0 * 60.0
    size = 50 * 1024 * 1024
    assert bitrate_sanity_reject_reason(size=size, quality="1080p", duration_sec=duration) is None


def test_pack_without_video_size_has_unknown_size():
    from mediatracker.providers.sources.comet import stream_file_size

    filename = "Ouvrage Démo S01 Complete 1080p.mkv"
    row = {"size": 40 * 1024**3}
    hints: dict = {}
    assert stream_file_size(row, hints, filename, file_index=2) is None
    media = _media(media_type="series", tmdb_runtime_min=22)
    item = SimpleNamespace(season=1, episode=4)
    candidate = LinkCandidate(
        url="magnet:x",
        kind="magnet",
        quality="1080p",
        size=None,
        filename=filename,
        season=1,
        episode=4,
        file_index=2,
        external_id="x",
    )
    assert filter_link_candidates(media, item, [candidate]) == [candidate]


def test_pack_uses_video_size_not_torrent_total():
    from mediatracker.providers.sources.comet import stream_file_size

    filename = "Ouvrage Démo S01 Complete 1080p.mkv"
    row = {"size": 40 * 1024**3}
    hints = {"videoSize": 50 * 1024 * 1024}
    assert stream_file_size(row, hints, filename, file_index=1) == 50 * 1024 * 1024
    media = _media(media_type="series", tmdb_runtime_min=22)
    item = SimpleNamespace(season=1, episode=1)
    candidate = LinkCandidate(
        url="magnet:x",
        kind="magnet",
        quality="1080p",
        size=50 * 1024 * 1024,
        filename=filename,
        season=1,
        episode=1,
        file_index=1,
        external_id="x",
    )
    assert filter_link_candidates(media, item, [candidate]) == [candidate]


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    yield session
    session.close()


def test_from_hosters_all_size_filtered_yields_size_sanity(db, monkeypatch):
    from mediatracker.core.pipeline import unlock as unlock_mod
    from mediatracker.core.quality_profile import default_profile
    from mediatracker.db.models import Download, Link, Media, MediaItem, Provider

    media = Media(type="series", title="Station Démo", year=2024, tmdb_runtime_min=22)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=1, episode=1, state="missing")
    db.add(item)
    db.flush()
    provider_id = db.scalar(select(Provider.id).limit(1))
    db.add(
        Link(
            media_item_id=item.id,
            provider_id=provider_id,
            kind="ddl_hoster",
            url="https://exemple.test/f",
            hoster="rapidgator",
            quality="1080p",
            language="VFF",
            size=40,
            external_id="tiny-hoster",
            priority=1,
        )
    )
    row = Download(media_item_id=item.id, state="pending")
    db.add(row)
    db.commit()
    monkeypatch.setattr(
        "mediatracker.core.profile_attempt_orchestration.resolve_download_poll_profile",
        lambda *_a, **_k: MagicMock(missing_explicit=False, profile=default_profile()),
    )
    monkeypatch.setattr(
        "mediatracker.core.profile_fallback.media_has_profile_repli_chain",
        lambda *_a, **_k: False,
    )
    unlock_mod._from_hosters(
        db,
        row,
        item,
        media,
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        ["rapidgator"],
        None,
        MagicMock(),
        MagicMock(),
        [],
        defer_final=False,
    )
    db.refresh(row)
    link = db.scalars(select(Link).limit(1)).first()
    assert row.error_code == "SIZE_SANITY"
    assert link is not None and link.dead is False

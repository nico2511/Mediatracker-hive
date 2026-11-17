"""Périmètre partagé : héritage d'intention sur épisodes ajoutés après coup."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from tests.conftest import PLAUSIBLE_VIDEO_BYTES, csrf, write_sparse_video_file

from mediatracker.core.storage import storage_layout
from mediatracker.db.models import (
    HiveFileFingerprint,
    HiveShareIntent,
    HiveShareScope,
    LibraryFile,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.hive.catalog import sync_hive_catalog
from mediatracker.hive.share_policy import HIVE_CATALOG_AUTO_SHARE_KEY

pytest_plugins = ["tests.test_hive_t1"]

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


def _hive_ready(client, app, settings):
    from tests.test_hive_catalog_sharing_worker import _enable_hive, _join_ruche, _origin_id

    _enable_hive(client)
    db = app.state.session_factory()
    try:
        _join_ruche(db, _origin_id(settings), bytes([0xE1] * 15 + [1]), settings)
        db.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="false"))
        db.commit()
    finally:
        db.close()


def _season_dir(layout, tmdb_id: int, season: int):
    folder = layout.series_dir / f"Serie démo (2099) [tmdbid-{tmdb_id}]" / f"Season {season:02d}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _seed_series(db, app, settings, *, tmdb_id: int, episodes: list[tuple[int, int]]):
    layout = storage_layout(app.state.session_factory(), settings)
    media = Media(type="series", title="Serie démo", year=2099, tmdb_id=tmdb_id)
    db.add(media)
    db.flush()
    items: list[MediaItem] = []
    for season, episode in episodes:
        folder = _season_dir(layout, tmdb_id, season)
        video = folder / f"S{season:02d}E{episode:02d}.mkv"
        write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES, prefix=f"x{season}{episode}".encode())
        item = MediaItem(
            media_id=media.id,
            season=season,
            episode=episode,
            title=f"Slot {episode}",
            state="downloaded",
            downloaded=True,
            file_path=str(video),
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=str(video),
                size=PLAUSIBLE_VIDEO_BYTES,
                parsed_title="Serie démo",
                parsed_year=2099,
                season=season,
                episode=episode,
                media_type="series",
                tmdb_id=tmdb_id,
                media_id=media.id,
                media_item_id=item.id,
                status="matched",
                confidence="high",
                seen_at=NOW,
            )
        )
        items.append(item)
    db.commit()
    return media, items


def _add_episode(db, app, settings, media: Media, *, tmdb_id: int, season: int, episode: int) -> MediaItem:
    layout = storage_layout(app.state.session_factory(), settings)
    folder = _season_dir(layout, tmdb_id, season)
    video = folder / f"S{season:02d}E{episode:02d}.mkv"
    write_sparse_video_file(video, PLAUSIBLE_VIDEO_BYTES, prefix=f"n{episode}".encode())
    item = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=f"Slot {episode}",
        state="downloaded",
        downloaded=True,
        file_path=str(video),
    )
    db.add(item)
    db.flush()
    db.add(
        LibraryFile(
            path=str(video),
            size=PLAUSIBLE_VIDEO_BYTES,
            parsed_title="Serie démo",
            parsed_year=2099,
            season=season,
            episode=episode,
            media_type="series",
            tmdb_id=tmdb_id,
            media_id=media.id,
            media_item_id=item.id,
            status="matched",
            confidence="high",
            seen_at=NOW,
        )
    )
    db.commit()
    return item


def test_season_scope_grants_intent_for_later_episode(client, app, settings):
    _hive_ready(client, app, settings)
    db = app.state.session_factory()
    try:
        media, items = _seed_series(db, app, settings, tmdb_id=99001, episodes=[(1, 1)])
        ep1_id = int(items[0].id)
        mid = int(media.id)
    finally:
        db.close()
    season_patch = client.patch(
        f"/api/v1/hive/media/{mid}/seasons/1/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    )
    assert season_patch.status_code == 204, season_patch.text
    db = app.state.session_factory()
    try:
        media = db.get(Media, mid)
        ep2 = _add_episode(db, app, settings, media, tmdb_id=99001, season=1, episode=2)
        ep2_id = int(ep2.id)
        assert db.get(HiveShareIntent, ep2_id) is None
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert db.get(HiveShareIntent, ep1_id) is not None
        assert db.get(HiveShareIntent, ep2_id) is not None
        assert db.get(HiveFileFingerprint, ep2_id) is not None
        assert db.scalar(select(HiveShareScope).where(HiveShareScope.media_id == mid, HiveShareScope.season == 1))
    finally:
        db.close()


def test_revoked_season_scope_stops_inheritance(client, app, settings):
    _hive_ready(client, app, settings)
    db = app.state.session_factory()
    try:
        media, _ = _seed_series(db, app, settings, tmdb_id=99002, episodes=[(1, 1)])
        mid = int(media.id)
    finally:
        db.close()
    assert client.patch(
        f"/api/v1/hive/media/{mid}/seasons/1/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204
    assert client.patch(
        f"/api/v1/hive/media/{mid}/seasons/1/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204
    db = app.state.session_factory()
    try:
        media = db.get(Media, mid)
        assert db.scalar(select(HiveShareScope).where(HiveShareScope.media_id == mid)) is None
        ep2 = _add_episode(db, app, settings, media, tmdb_id=99002, season=1, episode=2)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert db.get(HiveShareIntent, int(ep2.id)) is None
        assert db.get(HiveFileFingerprint, int(ep2.id)) is None
    finally:
        db.close()


def test_item_opt_out_not_regranted_under_season_scope(client, app, settings):
    _hive_ready(client, app, settings)
    db = app.state.session_factory()
    try:
        media, items = _seed_series(db, app, settings, tmdb_id=99003, episodes=[(1, 1), (1, 2)])
        ep1_id = int(items[0].id)
        mid = int(media.id)
    finally:
        db.close()
    assert client.patch(
        f"/api/v1/hive/media/{mid}/seasons/1/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204
    assert client.patch(
        f"/api/v1/hive/items/{ep1_id}/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204
    db = app.state.session_factory()
    try:
        media = db.get(Media, mid)
        ep3 = _add_episode(db, app, settings, media, tmdb_id=99003, season=1, episode=3)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert db.get(HiveShareIntent, ep1_id) is None
        assert db.get(HiveShareIntent, int(ep3.id)) is not None
        assert db.scalar(select(HiveShareScope).where(HiveShareScope.media_id == mid, HiveShareScope.season == 1))
    finally:
        db.close()


def test_fiche_scope_skips_excluded_season_inheritance(client, app, settings):
    _hive_ready(client, app, settings)
    db = app.state.session_factory()
    try:
        media, _ = _seed_series(db, app, settings, tmdb_id=99004, episodes=[(1, 1), (2, 1)])
        mid = int(media.id)
    finally:
        db.close()
    assert client.patch(
        f"/api/v1/hive/media/{mid}/sharing",
        json={"exclude_all": False, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204
    assert client.patch(
        f"/api/v1/hive/media/{mid}/seasons/2/sharing",
        json={"exclude_all": True, "excluded_ruche_ids": []},
        headers=csrf(client),
    ).status_code == 204
    db = app.state.session_factory()
    try:
        assert db.scalar(select(HiveShareScope).where(HiveShareScope.media_id == mid, HiveShareScope.season.is_(None)))
        blocked = db.scalar(select(HiveShareScope).where(HiveShareScope.media_id == mid, HiveShareScope.season == 2))
        assert blocked is not None
        media = db.get(Media, mid)
        ep_s1 = _add_episode(db, app, settings, media, tmdb_id=99004, season=1, episode=2)
        ep_s2 = _add_episode(db, app, settings, media, tmdb_id=99004, season=2, episode=2)
        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        assert db.get(HiveShareIntent, int(ep_s1.id)) is not None
        assert db.get(HiveShareIntent, int(ep_s2.id)) is None
    finally:
        db.close()
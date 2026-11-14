"""Jellyfin watched state must survive automatic library link after poll/webhook sync."""

from datetime import date, datetime, timezone

from mediatracker.db.models import JellyfinLibraryItem, Media, MediaItem
from mediatracker.integrations.jellyfin import JellyfinWatch
from mediatracker.integrations.jellyfin_sync import apply_watches
from mediatracker.library.jellyfin_align import _link_paths

NOW = datetime(2026, 10, 25, 14, 0, tzinfo=timezone.utc)
TMDB = 99001


def _series_with_episodes(db):
    media = Media(type="series", title="Exemple série", year=2026, tmdb_id=TMDB, created_at=NOW)
    db.add(media)
    db.flush()
    items: list[MediaItem] = []
    for ep in range(1, 4):
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=ep,
            air_date=date(2026, 8, 1),
            state="downloaded",
            downloaded=True,
            file_path=f"/srv/series/exemple-s01e0{ep}.mkv",
            jellyfin_item_id=f"jf-ep-{ep}",
        )
        db.add(item)
        items.append(item)
        db.add(
            JellyfinLibraryItem(
                jellyfin_id=f"jf-ep-{ep}",
                item_type="Episode",
                series_id="jf-series",
                season=1,
                episode=ep,
                played=False,
                path=f"/jellyfin/series/exemple-s01e0{ep}.mkv",
                local_path=f"/srv/series/exemple-s01e0{ep}.mkv",
                synced_at=NOW,
            )
        )
    db.commit()
    return media, items


def test_stale_cache_does_not_clear_watched_after_automatic_jellyfin_sync(client, app):
    """Poll/webhook sync then worker link_paths must keep Jellyfin vus (no manual resync)."""
    del client
    db = app.state.session_factory()
    try:
        media, items = _series_with_episodes(db)
        watches = [
            JellyfinWatch(
                item_id=f"jf-ep-{ep}",
                item_type="Episode",
                tmdb_id=TMDB,
                series_tmdb_id=TMDB,
                series_id="jf-series",
                season=1,
                episode=ep,
                path=f"/jellyfin/series/exemple-s01e0{ep}.mkv",
                played=True,
                user_id="user-1",
            )
            for ep in (1, 2)
        ]
        apply_watches(db, watches, None, NOW, user_filter="user-1")
        db.commit()
        for item in items[:2]:
            db.refresh(item)
            assert item.watched is True
        _link_paths(db, NOW)
        db.commit()
        for item in items[:2]:
            db.refresh(item)
            assert item.watched is True
        assert sum(1 for item in items if item.watched) == 2
        assert media.id is not None
    finally:
        db.close()

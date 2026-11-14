"""Mark episodes watched by hand. Jellyfin sync does not undo these."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from mediatracker.core.watch_manual_lock import stamp_manual_choice
from mediatracker.db.models import Media, MediaItem
from mediatracker.events import record_event


def mark_manual(
    db: Session,
    media: Media,
    *,
    item_id: int | None = None,
    season: int | None = None,
    until_season: int | None = None,
    until_episode: int | None = None,
    all_entire: bool = False,
    undo: bool = False,
) -> tuple[int, list[MediaItem]]:
    """Watch or undo one episode, one season, the whole series, or everything up to SxxEyy."""
    now = datetime.now(timezone.utc)
    rows = list(media.items)
    if all_entire:
        chosen = [item for item in rows if _entire_scope(item, media.type)]
    else:
        chosen = [item for item in rows if _selected(item, item_id, season, until_season, until_episode, media.type)]
    changed: list[MediaItem] = []
    for item in chosen:
        if undo:
            if not item.watched:
                continue
            item.watched = False
            item.watched_at = None
            item.watch_source = "manual"
            stamp_manual_choice(item, now)
            if item.state == "watched":
                item.state = "downloaded" if item.downloaded else "missing"
        else:
            if item.watched and item.watch_source == "manual":
                continue
            item.watched = True
            item.watched_at = item.watched_at or now
            item.watch_source = "manual"
            stamp_manual_choice(item, now)
            item.state = "watched"
            record_event(
                db,
                "item.watched",
                media_id=media.id,
                item_id=item.id,
                payload={"source": "manual", "season": item.season, "episode": item.episode},
            )
        changed.append(item)
    return len(changed), changed


def _entire_scope(item: MediaItem, media_type: str) -> bool:
    if media_type == "film":
        return item.season is None and item.episode is None
    if media_type == "series":
        return item.season is not None and item.episode is not None
    return False


def _selected(
    item: MediaItem,
    item_id: int | None,
    season: int | None,
    until_season: int | None,
    until_episode: int | None,
    media_type: str,
) -> bool:
    if item_id is not None:
        return item.id == item_id
    if media_type == "film":
        return item.season is None and item.episode is None
    if item.season is None or item.episode is None:
        return False
    if until_season is not None and until_episode is not None:
        return (item.season, item.episode) <= (until_season, until_episode)
    if season is not None:
        return item.season == season
    return False

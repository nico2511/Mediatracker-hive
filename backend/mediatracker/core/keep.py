"""Effective « Garder » flags for auto-cleanup and manual trash confirmation."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Media, MediaItem, MediaSeason

MIGRATION_FLAG_KEY = "cleanup_auto_default_migrated"
MIGRATION_NOTICE_KEY = "cleanup_keep_migration_notice"


def season_keep_map(db: Session, media_id: int) -> dict[int, MediaSeason]:
    return {
        row.number: row
        for row in db.scalars(select(MediaSeason).where(MediaSeason.media_id == media_id)).all()
    }


def season_stored_keep(db: Session, media_id: int, number: int | None) -> bool:
    if number is None:
        return False
    row = db.scalar(select(MediaSeason).where(MediaSeason.media_id == media_id, MediaSeason.number == number))
    return bool(row and row.keep)


def effective_keep(
    media: Media,
    season_number: int | None,
    item: MediaItem | None,
    seasons: dict[int, MediaSeason],
) -> bool:
    if media.keep:
        return True
    if season_number is not None:
        season = seasons.get(season_number)
        if season is not None and season.keep:
            return True
    if item is not None and item.keep:
        return True
    return False


def manual_trash_requires_confirm(
    db: Session,
    media: Media,
    items: list[MediaItem],
    *,
    scope: str,
    season: int | None,
    item_id: int | None,
) -> bool:
    seasons = season_keep_map(db, media.id)
    if scope == "media":
        targets = items
    elif scope == "season":
        targets = [row for row in items if row.season == season]
    elif scope == "episode":
        targets = [row for row in items if row.id == item_id]
    else:
        return True
    if not targets:
        return False
    return any(effective_keep(media, row.season, row, seasons) for row in targets)


def migrate_cleanup_auto_default(db: Session) -> int:
    """Idempotent startup marker. Existing fiches are never auto-marked « Garder »."""
    from datetime import datetime, timezone

    from mediatracker.db.models import Setting

    flag = db.get(Setting, MIGRATION_FLAG_KEY)
    if flag is not None and flag.value == "true":
        return 0

    now = datetime.now(timezone.utc)
    if flag is None:
        db.add(Setting(key=MIGRATION_FLAG_KEY, value="true", is_secret=False, updated_at=now))
    else:
        flag.value = "true"
    return 0

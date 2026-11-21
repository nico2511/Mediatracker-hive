"""« À voir » rows: unwatched items whose file is actually on disk (FileStatus present)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.file_status import FileStatusIndex
from mediatracker.core.media_hide import media_visible_clause
from mediatracker.db.models import Media, MediaItem

_DEFAULT_LIMIT = 50


def to_watch_pairs(db: Session, *, limit: int | None = _DEFAULT_LIMIT) -> list[tuple[MediaItem, Media]]:
    rows = db.execute(
        select(MediaItem, Media)
        .join(Media, Media.id == MediaItem.media_id)
        .where(MediaItem.watched.is_(False), media_visible_clause())
        .order_by(MediaItem.downloaded_at.desc(), MediaItem.id.desc())
    ).all()
    if not rows:
        return []
    items = [item for item, _media in rows]
    index = FileStatusIndex.load(db, items)
    picked: list[tuple[MediaItem, Media]] = []
    for item, media in rows:
        if index.for_item(item) != "present":
            continue
        picked.append((item, media))
        if limit is not None and len(picked) >= limit:
            break
    return picked

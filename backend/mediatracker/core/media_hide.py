"""Masquage réversible d'une fiche média (absence des listes, pas de suppression)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import ColumnElement, select
from sqlalchemy.orm import Session

from mediatracker.db.models import Media


def media_is_visible(media: Media) -> bool:
    return media.hidden_at is None


def media_visible_clause() -> ColumnElement[bool]:
    return Media.hidden_at.is_(None)


def hide_media(media: Media, *, moment: datetime | None = None) -> None:
    when = moment or datetime.now(timezone.utc)
    media.hidden_at = when


def unhide_media(media: Media) -> None:
    media.hidden_at = None


def unhide_on_follow(media: Media) -> None:
    """Suivre à nouveau réaffiche la fiche sans changer le suivi ailleurs."""
    if media.hidden_at is not None:
        unhide_media(media)


def hidden_media_ids(db: Session) -> set[int]:
    return set(db.scalars(select(Media.id).where(Media.hidden_at.is_not(None))).all())


def hidden_tmdb_keys(db: Session) -> set[tuple[str, int]]:
    rows = db.execute(
        select(Media.type, Media.tmdb_id).where(
            Media.hidden_at.is_not(None),
            Media.tmdb_id.is_not(None),
        )
    ).all()
    return {(media_type, int(tmdb_id)) for media_type, tmdb_id in rows}


def catalog_entry_linked_to_hidden(db: Session, media_id: int | None) -> bool:
    if media_id is None:
        return False
    return media_id in hidden_media_ids(db)
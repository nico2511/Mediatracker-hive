"""Stop following a film once it is obtained on disk."""

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.follow import unfollow_downloaded_film
from mediatracker.db.models import Media


def stop_following_downloaded_film(
    db: Session,
    media: Media,
    *,
    reason: str,
    emit_event: bool = True,
    settings: Settings | None = None,
) -> bool:
    """Unfollow a film that is already downloaded. Files and history stay."""
    return unfollow_downloaded_film(db, media, reason=reason, emit_event=emit_event, settings=settings)

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
    download_id: int | None = None,
    instrument_metro: bool = True,
) -> bool:
    """Unfollow a film that is already downloaded. Files and history stay."""
    ok = unfollow_downloaded_film(db, media, reason=reason, emit_event=emit_event, settings=settings)
    if ok and instrument_metro:
        from mediatracker.metro.instrument import instrument_sui3_stop_follow
        from mediatracker.metro.record import safe_instrument

        safe_instrument(
            instrument_sui3_stop_follow,
            db=db,
            media=media,
            settings=settings,
            download_id=download_id,
        )
    return ok

"""When a fiche was last inspected (sources, library, Jellyfin)."""

from __future__ import annotations

from datetime import datetime, timezone

from mediatracker.db.models import Media


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def touch_fiche_checked(media: Media | None, moment: datetime | None = None) -> None:
    """Record a library scan, Jellyfin sync, or source check on the fiche timeline."""
    if media is None:
        return
    when = moment or datetime.now(timezone.utc)
    current = media.last_repair_at
    if current is None or _aware(current) < _aware(when):
        media.last_repair_at = when


def fiche_last_check_at(media: Media) -> datetime | None:
    """Best timestamp for « Dernière vérification » on the fiche."""
    stamps: list[datetime] = []
    for value in (media.last_source_poll_at, media.last_repair_at):
        if value is not None:
            stamps.append(_aware(value))
    return max(stamps) if stamps else None

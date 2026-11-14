"""When a manual watch choice blocks Jellyfin, and when a newer playback wins."""

from datetime import datetime, timezone

from mediatracker.db.models import MediaItem


def stamp_manual_choice(item: MediaItem, when: datetime) -> None:
    item.watch_manual_at = when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc)


def jellyfin_blocks_manual(item: MediaItem, jellyfin_played: bool, jellyfin_at: datetime | None) -> bool:
    """True when Jellyfin must not change ``watched`` on this item."""
    if item.watch_source != "manual" or item.watched == jellyfin_played:
        return False
    if item.watched and not jellyfin_played:
        return True
    manual_at = _aware(item.watch_manual_at)
    if not item.watched and jellyfin_played:
        if jellyfin_at is None:
            return True
        if manual_at is None:
            return True
        return _aware(jellyfin_at) <= manual_at
    return True


def clear_manual_lock(item: MediaItem) -> None:
    item.watch_source = "auto"
    item.watch_manual_at = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

"""Links that open an item in the Jellyfin web client."""

from urllib.parse import quote

from sqlalchemy.orm import Session

from mediatracker.db.models import Media, MediaItem, Setting


def jellyfin_base_url(db: Session) -> str:
    row = db.get(Setting, "jellyfin_url")
    if row is None or not row.value:
        return ""
    return row.value.strip().rstrip("/")


def jellyfin_web_url(base: str | None, item_id: str | None) -> str | None:
    """``/web/index.html#!/details?id=`` for one Jellyfin item. No API key."""
    root = (base or "").strip().rstrip("/")
    identity = (item_id or "").strip()
    if not root or not identity:
        return None
    return f"{root}/web/index.html#!/details?id={quote(identity, safe='')}&context=home"


def jellyfin_home_url(base: str | None) -> str | None:
    root = (base or "").strip().rstrip("/")
    if not root:
        return None
    return f"{root}/web/index.html"


def playable_item_id(media: Media, episodes: list[MediaItem]) -> str | None:
    """Series id when the fiche is linked, otherwise the first episode Jellyfin knows."""
    series = (media.jellyfin_series_id or "").strip()
    if series:
        return series
    for item in episodes:
        identity = (item.jellyfin_item_id or "").strip()
        if identity:
            return identity
    return None

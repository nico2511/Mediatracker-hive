"""Read Jellyfin's library and whether a scan is already running.

Alignment must not run during RefreshLibrary. Played state is optional: a file
counts as present even when UserData has no flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from mediatracker.integrations.jellyfin import (
    JellyfinClient,
    JellyfinError,
    _as_int,
    _first_int,
    _last_played_at,
    _played_state,
    _text_id,
    parse_jellyfin_datetime,
)

_KINDS = frozenset({"Movie", "Series", "Season", "Episode"})
_IDLE = frozenset({"", "idle", "none", "null"})


@dataclass(frozen=True)
class ScanStatus:
    scanning: bool
    progress: int | None
    message: str


@dataclass(frozen=True)
class CatalogItem:
    jellyfin_id: str
    item_type: str
    name: str | None
    year: int | None
    series_id: str | None
    parent_id: str | None
    tmdb_id: int | None
    imdb_id: str | None
    tvdb_id: int | None
    path: str | None
    season: int | None
    episode: int | None
    played: bool | None
    last_played_at: datetime | None
    date_last_saved: datetime | None


def read_scan_status(client: JellyfinClient) -> ScanStatus:
    """RefreshLibrary wins. Virtual folder RefreshStatus covers a server that hides the task."""
    tasks: list[dict] | None
    try:
        tasks = client.scheduled_tasks()
    except JellyfinError:
        tasks = None
    if tasks:
        for task in tasks:
            if not _is_refresh_task(task):
                continue
            progress = _progress(task.get("CurrentProgressPercentage"))
            state = str(task.get("State") or "")
            if state in {"Running", "Cancelling"}:
                text = "Jellyfin analyse la bibliothèque."
                if progress is not None:
                    text = f"Jellyfin analyse la bibliothèque ({progress} %)."
                return ScanStatus(True, progress, text)
            return ScanStatus(False, None, "Jellyfin n'analyse pas la bibliothèque.")
    try:
        folders = client.virtual_folders()
    except JellyfinError:
        if tasks is None:
            return ScanStatus(False, None, "Jellyfin est injoignable.")
        folders = []
    for folder in folders:
        status = str(folder.get("RefreshStatus") or "").strip()
        if status.casefold() not in _IDLE:
            return ScanStatus(True, None, "Jellyfin analyse un dossier.")
    if tasks is None:
        return ScanStatus(False, None, "Jellyfin est injoignable.")
    return ScanStatus(False, None, "Jellyfin n'analyse pas la bibliothèque.")


def parse_catalog_item(raw: dict) -> CatalogItem | None:
    """One Items row. Episode TMDB ids stay on the episode; they are not a series id."""
    item_type = str(raw.get("Type") or "")
    if item_type not in _KINDS:
        return None
    item_id = raw.get("Id")
    if not isinstance(item_id, str) or not item_id.strip():
        return None
    providers = raw.get("ProviderIds") if isinstance(raw.get("ProviderIds"), dict) else {}
    user_data = raw.get("UserData") if isinstance(raw.get("UserData"), dict) else {}
    played, _percent = _played_state(user_data, raw)
    series_id = raw.get("SeriesId") if isinstance(raw.get("SeriesId"), str) and raw.get("SeriesId") else None
    if item_type in {"Series", "Movie"}:
        series_id = item_id
    season = None
    episode = None
    if item_type == "Episode":
        season = _first_int(raw.get("ParentIndexNumber"))
        episode = _first_int(raw.get("IndexNumber"))
    elif item_type == "Season":
        season = _first_int(raw.get("IndexNumber"))
    name = raw.get("Name") if isinstance(raw.get("Name"), str) else None
    parent = raw.get("ParentId") if isinstance(raw.get("ParentId"), str) else None
    return CatalogItem(
        jellyfin_id=item_id.strip(),
        item_type=item_type,
        name=name,
        year=_as_int(raw.get("ProductionYear")),
        series_id=series_id,
        parent_id=parent,
        tmdb_id=_first_int(providers.get("Tmdb"), providers.get("TMDB")),
        imdb_id=_text_id(providers.get("Imdb") or providers.get("IMDB")),
        tvdb_id=_first_int(providers.get("Tvdb"), providers.get("TVDB")),
        path=raw.get("Path") if isinstance(raw.get("Path"), str) else None,
        season=season,
        episode=episode,
        played=played,
        last_played_at=_last_played_at(user_data, raw),
        date_last_saved=parse_saved(raw.get("DateLastSaved")),
    )


def parse_saved(value: object) -> datetime | None:
    parsed = parse_jellyfin_datetime(value)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_refresh_task(task: dict) -> bool:
    key = str(task.get("Key") or "")
    if key == "RefreshLibrary" or key.endswith(".RefreshLibrary"):
        return True
    folded = str(task.get("Name") or "").casefold()
    return "refresh library" in folded or "scan media library" in folded or "actualiser la bibliothèque" in folded


def _progress(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(0, min(100, int(value)))

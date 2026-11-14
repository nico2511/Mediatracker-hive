"""Upload a magnet only when AllDebrid already has it cached.

The instant check does not add anything. A magnet that is not cached is never
uploaded. If an upload still is not ready within a short wait, it is deleted
before the next candidate. Uptobox links inside the torrent are skipped.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS
from mediatracker.providers.base import DirectLink
from mediatracker.providers.debrid.alldebrid import AllDebridError, MagnetFile
from mediatracker.providers.sources.manual import hoster_of

_VIDEO = {".mkv", ".mp4", ".avi", ".m4v"}
_BTIH = re.compile(r"btih:([a-zA-Z0-9]+)", re.IGNORECASE)
CACHE_WAIT_SECONDS = 8.0
CACHE_POLL_SECONDS = 2.0


class ClaimedMagnet:
    def __init__(self, direct: DirectLink, magnet_id: str, file_link: str) -> None:
        self.direct = direct
        self.magnet_id = magnet_id
        self.file_link = file_link


def claim_cached_magnet(
    debrid,
    magnet: str,
    *,
    file_index: int | None,
    filename: str | None,
    wait_seconds: float = CACHE_WAIT_SECONDS,
    poll_interval: float = CACHE_POLL_SECONDS,
    sleeper=time.sleep,
) -> ClaimedMagnet | None:
    """Return a direct link for a cached magnet, or None. Non-cached uploads are deleted."""
    if _known_uncached(debrid, magnet):
        return None
    if not hasattr(debrid, "upload_magnet"):
        return None
    uploaded = debrid.upload_magnet(magnet)
    magnet_id = str(getattr(uploaded, "id", "") or "")
    if not magnet_id:
        return None
    snapshot = uploaded if getattr(uploaded, "ready", False) else _wait_ready(
        debrid, magnet_id, wait_seconds=wait_seconds, poll_interval=poll_interval, sleeper=sleeper
    )
    if snapshot is None:
        _delete(debrid, magnet_id)
        return None
    chosen = pick_magnet_file(list(getattr(snapshot, "files", []) or []), file_index, filename)
    if chosen is None or not chosen.link:
        _delete(debrid, magnet_id)
        return None
    try:
        direct = debrid.unlock(chosen.link)
    except AllDebridError:
        _delete(debrid, magnet_id)
        raise
    if direct is None or not getattr(direct, "url", None):
        _delete(debrid, magnet_id)
        return None
    return ClaimedMagnet(direct if isinstance(direct, DirectLink) else direct, magnet_id, chosen.link)


def pick_magnet_file(
    files: list[MagnetFile],
    file_index: int | None,
    filename: str | None,
) -> MagnetFile | None:
    usable = [item for item in files if item.link and _video(item.name) and not _blocked(item.link)]
    if filename:
        wanted = Path(filename).name.casefold()
        for item in usable:
            if Path(item.name).name.casefold() == wanted:
                return item
    if file_index is not None:
        for item in usable:
            if item.index == file_index:
                return item
    if len(usable) == 1:
        return usable[0]
    return None


def _known_uncached(debrid, magnet: str) -> bool:
    """True when the instant check answered and this magnet is not cached."""
    if not hasattr(debrid, "magnet_cache"):
        return False
    try:
        hits = debrid.magnet_cache([magnet])
    except AllDebridError as exc:
        if exc.code == "AUTH_BLOCKED":
            raise
        return False
    if not hits:
        return False
    wanted = _btih(magnet)
    for hit in hits:
        echoed = _btih(getattr(hit, "magnet", "") or "")
        if echoed and echoed != wanted:
            continue
        return not bool(getattr(hit, "cached", False))
    return False


def _wait_ready(debrid, magnet_id: str, *, wait_seconds: float, poll_interval: float, sleeper):
    if not hasattr(debrid, "magnet_status"):
        return None
    deadline = time.monotonic() + max(0.0, wait_seconds)
    while True:
        status = debrid.magnet_status(magnet_id)
        if getattr(status, "ready", False):
            return status
        if time.monotonic() >= deadline:
            return None
        sleeper(min(poll_interval, max(0.0, deadline - time.monotonic())))


def _delete(debrid, magnet_id: str) -> None:
    if hasattr(debrid, "delete_magnet"):
        debrid.delete_magnet(magnet_id)


def _blocked(url: str) -> bool:
    host = (hoster_of(url) or "").casefold()
    return host in BLOCKED_SOURCE_HOSTERS


def _video(name: str) -> bool:
    return Path(name).suffix.lower() in _VIDEO


def _btih(magnet: str) -> str:
    match = _BTIH.search(magnet or "")
    return match.group(1).lower() if match else (magnet or "").strip().lower()

"""Checks that run before a hoster link is handed to aria2.

The order is fixed:

1. ``magnet_cache_first`` — extension point. When the item has magnet or torrent
   links, AllDebrid's cache is asked first (``/v4/magnet/instant``). Nothing is
   added to the account and nothing is sent to aria2. A later milestone can
   prefer a cached magnet over hoster links; until then the answer is only
   observed.
2. The local library (the episode flag and the scanner table). A hit is
   « déjà présent ».
3. AllDebrid saved links and recent history, same URL or same filename.
4. ``/v4/link/infos``. ``LINK_DOWN`` and « host under maintenance » try the
   next hoster at once. Another retryable AllDebrid error stays on this link.

Direct CDN URLs are never logged or stored.
"""

import logging
import time
from pathlib import Path
from threading import Lock

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.forget import is_file_absent
from mediatracker.core.pipeline.constants import host_down
from mediatracker.db.models import Download, LibraryFile, Link, MediaItem
from mediatracker.providers.debrid.alldebrid import AllDebridError

logger = logging.getLogger(__name__)

PRESENT = "déjà présent"
OFFLINE = "hors ligne"
KNOWN_FILES_TTL = 300.0
DUPLICATE_IGNORED = "Doublon réel ignoré."
REDOWNLOAD_STARTED = "Retéléchargement lancé."
NO_LINK_RETRY_LATER = "Aucun lien disponible, nouvel essai plus tard."

_cache_lock = Lock()
_known_cache: dict[str, tuple[float, list, bool]] = {}


def clear_known_files_cache() -> None:
    """Drop the process-wide AllDebrid inventory. Call this after a purge."""
    with _cache_lock:
        _known_cache.clear()


def _known_key(debrid) -> str:
    kind = getattr(debrid, "type", None)
    if isinstance(kind, str) and kind:
        return kind
    return f"{type(debrid).__name__}:{id(debrid)}"


class HosterDecision:
    def __init__(
        self,
        action: str,
        *,
        code: str | None = None,
        message: str | None = None,
        filename: str | None = None,
        size: int | None = None,
    ) -> None:
        self.action = action
        self.code = code
        self.message = message
        self.filename = filename
        self.size = size


def magnet_cache_first(db: Session, debrid, item: MediaItem) -> str | None:
    """Run the magnet cache check before any hoster link.

    Returns ``"paused"`` when AllDebrid blocked the account. Any other failure
    is ignored so hoster links still proceed. An empty magnet list does not
    call the network.
    """
    if debrid is None or not hasattr(debrid, "magnet_cache"):
        return None
    rows = db.scalars(
        select(Link).where(
            Link.media_item_id == item.id,
            Link.kind.in_(("magnet", "torrent")),
            Link.dead.is_(False),
        )
    ).all()
    magnets = [row.url for row in rows if row.url]
    if not magnets:
        return None
    from mediatracker.db.session import commit_retry

    commit_retry(db)
    try:
        debrid.magnet_cache(magnets)
    except AllDebridError as exc:
        if exc.code == "AUTH_BLOCKED":
            return "paused"
        logger.info("Contrôle du cache aimant ignoré (%s).", exc.code)
    return None


def library_duplicate(db: Session, item: MediaItem) -> LibraryFile | None:
    """Scanner row whose path is still a non-empty video. Sidecars and ghosts do not count."""
    from mediatracker.core.library_video import valid_video

    rows = db.scalars(
        select(LibraryFile).where(
            LibraryFile.media_item_id == item.id,
            LibraryFile.status == "matched",
            LibraryFile.missing_at.is_(None),
        )
    ).all()
    for row in rows:
        if valid_video(Path(row.path)):
            return row
    return None


def load_known_files(debrid, *, now: float | None = None) -> tuple[list, bool]:
    """Saved links plus history, cached for at least five minutes.

    ``True`` means the queue must pause (AUTH_BLOCKED). A new client each tick
    still hits this cache: the key is the provider type, not the instance.
    """
    if debrid is None or not hasattr(debrid, "known_files"):
        return [], False
    moment = time.monotonic() if now is None else now
    key = _known_key(debrid)
    with _cache_lock:
        cached = _known_cache.get(key)
        if cached is not None and moment - cached[0] < KNOWN_FILES_TTL:
            return cached[1], cached[2]
    try:
        files: list = list(debrid.known_files())
        blocked = False
    except AllDebridError as exc:
        if exc.code == "AUTH_BLOCKED":
            files, blocked = [], True
        else:
            logger.info("Historique AllDebrid indisponible (%s).", exc.code)
            return [], False
    with _cache_lock:
        _known_cache[key] = (moment, files, blocked)
    return files, blocked


def should_bypass_remote_dedup(row: Download, item: MediaItem) -> bool:
    """Skip AllDebrid saved-link dedupe for a forced job or a file missing on disk."""
    return bool(row.forced) or is_file_absent(item)


def decide_hoster(debrid, url: str, filename: str | None, known: list) -> HosterDecision:
    """Online check and remote dedupe for one hoster URL. Does not unlock it."""
    if _remote_duplicate(known, url, filename):
        return HosterDecision("present", code="ALREADY_PRESENT", message=PRESENT, filename=filename)
    if debrid is None or not hasattr(debrid, "inspect_link"):
        return HosterDecision("proceed", filename=filename)
    try:
        info = debrid.inspect_link(url)
    except AllDebridError as exc:
        if exc.code == "AUTH_BLOCKED":
            return HosterDecision("paused", code="AUTH_BLOCKED", message=exc.message)
        if host_down(exc.code, exc.message):
            return HosterDecision("offline", code=exc.code, message=exc.message)
        if exc.retryable:
            return HosterDecision("retry", code=exc.code, message=exc.message)
        return HosterDecision("offline", code=exc.code or "LINK_DOWN", message=OFFLINE)
    if info.code == "AUTH_BLOCKED":
        return HosterDecision("paused", code="AUTH_BLOCKED", message=info.message)
    if host_down(info.code, info.message):
        return HosterDecision("offline", code=info.code or "LINK_HOST_UNAVAILABLE", message=info.message)
    if info.retryable:
        return HosterDecision("retry", code=info.code, message=info.message)
    if not info.online:
        code = info.code or "LINK_DOWN"
        message = OFFLINE if code == "LINK_DOWN" else (info.message or OFFLINE)
        return HosterDecision("offline", code=code, message=message)
    name = info.filename or filename
    if _remote_duplicate(known, url, name, info.size):
        return HosterDecision("present", code="ALREADY_PRESENT", message=PRESENT, filename=name, size=info.size)
    return HosterDecision("proceed", filename=name, size=info.size)


def _remote_duplicate(known: list, url: str, filename: str | None, size: int | None = None) -> bool:
    wanted_url = _norm_url(url)
    wanted_name = _norm_name(filename)
    for entry in known:
        link = getattr(entry, "link", "") or ""
        if wanted_url and _norm_url(link) == wanted_url:
            return True
        entry_name = _norm_name(getattr(entry, "filename", None))
        if not wanted_name or not entry_name or wanted_name != entry_name:
            continue
        entry_size = getattr(entry, "size", None)
        if size and entry_size and int(size) != int(entry_size):
            continue
        return True
    return False


def _norm_url(url: str | None) -> str:
    if not url:
        return ""
    return url.strip().rstrip("/")


def _norm_name(filename: str | None) -> str:
    if not filename:
        return ""
    name = Path(filename).name.strip().casefold()
    if name in {"", "video.mkv", "video.mp4"}:
        return ""
    return name

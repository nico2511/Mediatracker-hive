"""Disk cache for TMDB CDN images (image.tmdb.org). No API token is used or logged."""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from mediatracker.config import Settings
from mediatracker.core.outbound_http import borrow_http_client
from mediatracker.integrations.tmdb import TMDB_IMAGE_BASE

logger = logging.getLogger(__name__)

KINDS = frozenset({"poster", "backdrop", "still"})
SIZES = frozenset({"w92", "w185", "w342", "w780", "original"})
_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(?:jpe?g|png|webp)$", re.IGNORECASE)
_TMDB_CDN = f"{TMDB_IMAGE_BASE}/t/p"
_CACHE_CONTROL = "public, max-age=31536000, immutable"
_STAMP = "image_cache_purge_at"
_LOCKS: dict[str, threading.Lock] = {}
_LOCK_GUARD = threading.Lock()
_CLIENT_KEY = ("tmdb_cdn", urlparse(TMDB_IMAGE_BASE).netloc)
_TOUCH_MIN_AGE = timedelta(hours=24)


class ImageCacheError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class CachedImage:
    path: Path
    etag: str
    media_type: str


@dataclass(frozen=True)
class PurgeReport:
    removed_stale: int
    removed_lru: int
    bytes_freed: int


def cache_root(settings: Settings) -> Path:
    return Path(settings.config_dir) / "cache" / "images"


def validate_filename(name: str) -> str:
    base = Path(name).name
    if base != name or ".." in name or "/" in name or "\\" in name:
        raise ImageCacheError("BAD_NAME", "Nom de fichier invalide.")
    if not _FILENAME.fullmatch(base):
        raise ImageCacheError("BAD_NAME", "Nom de fichier invalide.")
    return base


def validate_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ImageCacheError("BAD_KIND", "Type d'image inconnu.")
    return kind


def validate_size(size: str) -> str:
    if size not in SIZES:
        raise ImageCacheError("BAD_SIZE", "Taille d'image inconnue.")
    return size


def cache_file(root: Path, kind: str, size: str, filename: str) -> Path:
    return root / kind / size / filename


def _media_type(filename: str) -> str:
    lower = filename.casefold()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def _etag(path: Path) -> str:
    stat = path.stat()
    return f'W/"{stat.st_size}-{int(stat.st_mtime_ns)}"'


def _lock_for(key: str) -> threading.Lock:
    with _LOCK_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[key] = lock
        return lock


def touch_if_stale(path: Path, now: datetime | None = None) -> bool:
    """Bump mtime at most once per day so purge/LRU stay accurate without hot-path writes."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    try:
        stat = path.stat()
    except OSError:
        return False
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    if moment - mtime < _TOUCH_MIN_AGE:
        return False
    try:
        path.touch()
    except OSError:
        return False
    return True


def resolve_cached(
    settings: Settings,
    kind: str,
    size: str,
    filename: str,
    *,
    fetch: bool = True,
    touch: bool = True,
) -> CachedImage:
    """Return a cached file, optionally fetching from TMDB first."""
    kind = validate_kind(kind)
    size = validate_size(size)
    filename = validate_filename(filename)
    root = cache_root(settings)
    dest = cache_file(root, kind, size, filename)
    if dest.is_file():
        if touch:
            touch_if_stale(dest)
        return CachedImage(dest, _etag(dest), _media_type(filename))
    if not fetch:
        raise ImageCacheError("NOT_FOUND", "Image absente du cache.")
    key = str(dest)
    with _lock_for(key):
        if dest.is_file():
            if touch:
                touch_if_stale(dest)
            return CachedImage(dest, _etag(dest), _media_type(filename))
        data, content_type = _download(size, filename)
        _write_atomic(dest, data)
        media = content_type.split(";", 1)[0].strip() if content_type else _media_type(filename)
        return CachedImage(dest, _etag(dest), media)


def _download(size: str, filename: str) -> tuple[bytes, str | None]:
    url = f"{_TMDB_CDN}/{size}/{filename}"
    client, owned = borrow_http_client(_CLIENT_KEY, httpx.Client, timeout=30.0, follow_redirects=True)
    try:
        response = client.get(url)
    except httpx.HTTPError:
        raise ImageCacheError("TRANSPORT", "Le CDN d'images est injoignable.") from None
    finally:
        if owned:
            client.close()
    if response.status_code == 404:
        raise ImageCacheError("NOT_FOUND", "Image introuvable sur TMDB.")
    if response.status_code >= 400:
        raise ImageCacheError("BAD_RESPONSE", "Le CDN d'images a refusé la requête.")
    body = response.content
    if not body:
        raise ImageCacheError("BAD_RESPONSE", "Réponse vide.")
    return body, response.headers.get("Content-Type")


def _write_atomic(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(data)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def maybe_purge_cache(settings: Settings, db, *, now: datetime | None = None) -> PurgeReport | None:
    """Drop stale entries and enforce the size cap. Runs at most once per UTC day."""
    from mediatracker.db.models import Setting

    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    row = db.get(Setting, _STAMP)
    if row is not None and row.value:
        try:
            last = datetime.fromisoformat(row.value)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if moment - last < timedelta(days=1):
                return None
        except ValueError:
            pass
    report = purge_cache(settings, now=moment)
    stamp = moment.isoformat()
    if row is None:
        db.add(Setting(key=_STAMP, value=stamp, is_secret=False, updated_at=moment))
    else:
        row.value = stamp
        row.updated_at = moment
    if report.removed_stale or report.removed_lru:
        logger.info(
            "Cache images : %s fichier(s) retiré(s) (%s obsolètes, %s LRU), %s octet(s) libérés.",
            report.removed_stale + report.removed_lru,
            report.removed_stale,
            report.removed_lru,
            report.bytes_freed,
        )
    return report


def purge_cache(settings: Settings, *, now: datetime | None = None) -> PurgeReport:
    """Manual purge: stale files first, then LRU until under the size cap."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    root = cache_root(settings)
    if not root.is_dir():
        return PurgeReport(0, 0, 0)
    stale_cutoff = moment - timedelta(days=max(1, int(settings.image_cache_stale_days)))
    max_bytes = max(1, int(settings.image_cache_max_bytes))
    entries: list[tuple[Path, int, float]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entries.append((path, stat.st_size, stat.st_mtime))
    removed_stale = 0
    freed = 0
    kept: list[tuple[Path, int, float]] = []
    for path, size, mtime in entries:
        accessed = datetime.fromtimestamp(mtime, tz=timezone.utc)
        if accessed < stale_cutoff:
            if _unlink(path):
                removed_stale += 1
                freed += size
            continue
        kept.append((path, size, mtime))
    total = sum(size for _, size, _ in kept)
    removed_lru = 0
    if total > max_bytes:
        kept.sort(key=lambda row: row[2])
        for path, size, _mtime in kept:
            if total <= max_bytes:
                break
            if _unlink(path):
                removed_lru += 1
                freed += size
                total -= size
    return PurgeReport(removed_stale, removed_lru, freed)


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    parent = path.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            grand = parent.parent
            if grand.is_dir() and not any(grand.iterdir()):
                grand.rmdir()
    except OSError:
        pass
    return True

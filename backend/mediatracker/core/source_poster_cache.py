"""Cache on disk for source listing posters (same-origin proxy, no hotlink)."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.outbound_http import borrow_http_client
from mediatracker.db.models import CatalogEntry
from mediatracker.integrations.tmdb import TMDB_IMAGE_BASE
from mediatracker.security.ssrf import OutboundUrlRejected, validate_outbound_url_static

logger = logging.getLogger(__name__)

_CACHE_CONTROL = "private, max-age=86400"
_RELATIVE = re.compile(r"^/+")
_MAX_BYTES = 5 * 1024 * 1024
_FETCH_TIMEOUT = 8.0
_FAIL_TTL = timedelta(hours=2)
_TRANSIENT_FAIL_TTL = timedelta(minutes=5)
_TRANSIENT_CODES = frozenset({"TRANSPORT", "BAD_RESPONSE"})
_LOCK_WAIT_SECONDS = 8.0
_TMDB_IMAGE_HOST = urlparse(TMDB_IMAGE_BASE).netloc

_STRICT_BLOCKED = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("fc00::/7"),
)
_METADATA_ADDRS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)

_lock_guard = threading.Lock()
_entry_locks: dict[int, threading.Lock] = {}


class SourcePosterError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def source_poster_api_url(entry_id: int) -> str:
    return f"/api/v1/images/source/{entry_id}"


def cache_root(settings: Settings) -> Path:
    return Path(settings.config_dir) / "cache" / "source-posters"


def cache_path(settings: Settings, entry_id: int) -> Path:
    return cache_root(settings) / f"{entry_id}.bin"


def cache_control_header() -> str:
    return _CACHE_CONTROL


def _source_base_url(db: Session, settings: Settings, source: str) -> str | None:
    from mediatracker.providers.sources.loading import source_base_url

    base = source_base_url(db, settings, source)
    if base:
        return base.rstrip("/") + "/"
    return None


def _poster_referer_origin(db: Session, settings: Settings, row: CatalogEntry) -> str | None:
    if not row.source:
        return None
    base = _source_base_url(db, settings, row.source)
    if not base:
        return None
    parsed = urlparse(base)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/"


def _poster_fetch_headers(db: Session, settings: Settings, row: CatalogEntry) -> dict[str, str]:
    headers: dict[str, str] = {}
    referer = _poster_referer_origin(db, settings, row)
    if referer:
        headers["Referer"] = referer
    if row.source == "wawacity":
        from mediatracker.db.models import Setting
        from mediatracker.providers.sources.wawacity import DEFAULT_USER_AGENT

        stored = db.get(Setting, "wawacity_user_agent")
        agent = (
            (stored.value if stored and stored.value else None)
            or settings.wawacity_user_agent
            or DEFAULT_USER_AGENT
        )
        headers["User-Agent"] = agent
    return headers


def allowed_poster_hosts(db: Session, settings: Settings, source: str) -> frozenset[str]:
    hosts = {_TMDB_IMAGE_HOST}
    base = _source_base_url(db, settings, source)
    if base:
        hostname = (urlparse(base).hostname or "").lower().rstrip(".")
        if hostname:
            hosts.add(hostname)
    return frozenset(hosts)


def normalize_source_poster_url(
    db: Session,
    settings: Settings,
    row: CatalogEntry,
    raw: str | None,
) -> str | None:
    """Make relative paths absolute; reject absolute URLs whose host is not allowed."""
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    allowed = allowed_poster_hosts(db, settings, row.source)
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower().rstrip(".")
        if host not in allowed:
            path = parsed.path or ""
            if path.startswith("/img/"):
                base = _source_base_url(db, settings, row.source)
                if base:
                    joined = urljoin(base, path.lstrip("/"))
                    rebased_host = (urlparse(joined).hostname or "").lower().rstrip(".")
                    if rebased_host in allowed:
                        return joined
            return None
        return text
    if _RELATIVE.match(text):
        base = _source_base_url(db, settings, row.source)
        if not base:
            return None
        joined = urljoin(base, text.lstrip("/"))
        host = (urlparse(joined).hostname or "").lower().rstrip(".")
        if host not in allowed:
            return None
        return joined
    return None


def _strict_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip in _METADATA_ADDRS:
        return True
    for net in _STRICT_BLOCKED:
        if ip in net:
            return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved


def _check_poster_resolved_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _strict_ip_blocked(ip):
            raise SourcePosterError("SSRF", "Adresse réseau refusée pour une affiche source.")


def enforce_poster_fetch_url(url: str, allowed_hosts: frozenset[str]) -> str:
    try:
        text = validate_outbound_url_static(url)
    except OutboundUrlRejected as exc:
        raise SourcePosterError("SSRF", str(exc)) from exc
    host = (urlparse(text).hostname or "").lower().rstrip(".")
    if not host or host not in allowed_hosts:
        raise SourcePosterError("SSRF", "Hôte d'affiche non autorisé.")
    if not _literal_ip_blocked(host):
        _check_poster_resolved_host(host)
    return text


def _literal_ip_blocked(host: str) -> bool:
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return _strict_ip_blocked(ipaddress.ip_address(candidate))
    except ValueError:
        return False


def _poster_request_hooks(allowed_hosts: frozenset[str]) -> dict[str, list]:
    def _on_request(request: httpx.Request) -> None:
        enforce_poster_fetch_url(str(request.url), allowed_hosts)

    return {"request": [_on_request]}


def _detect_image_type(data: bytes) -> str | None:
    if len(data) >= 3 and data[0:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 8 and data[0:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 6 and data[0:6] in {b"GIF87a", b"GIF89a"}:
        return "image/gif"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def fetch_source_poster(
    settings: Settings,
    url: str,
    *,
    allowed_hosts: frozenset[str],
    headers: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    clean = enforce_poster_fetch_url(url, allowed_hosts)
    client, owns = borrow_http_client(
        ("source_poster", urlparse(clean).hostname or "", ""),
        httpx.Client,
        timeout=_FETCH_TIMEOUT,
        follow_redirects=True,
        event_hooks=_poster_request_hooks(allowed_hosts),
    )
    try:
        with client.stream("GET", clean, headers=headers or {}) as response:
            if response.status_code == 404:
                raise SourcePosterError("NOT_FOUND", "Affiche introuvable sur la source.")
            if response.status_code >= 400:
                raise SourcePosterError("BAD_RESPONSE", f"La source a répondu {response.status_code}.")
            raw_length = response.headers.get("content-length")
            if raw_length:
                try:
                    if int(raw_length) > _MAX_BYTES:
                        raise SourcePosterError("TOO_LARGE", "Affiche trop volumineuse.")
                except ValueError:
                    pass
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                size += len(chunk)
                if size > _MAX_BYTES:
                    raise SourcePosterError("TOO_LARGE", "Affiche trop volumineuse.")
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        raise SourcePosterError("TRANSPORT", "Affiche source injoignable.") from exc
    finally:
        if owns:
            client.close()
    content = b"".join(chunks)
    if not content:
        raise SourcePosterError("NOT_FOUND", "Affiche vide.")
    media_type = _detect_image_type(content)
    if media_type is None:
        raise SourcePosterError("NOT_IMAGE", "Format d'image non supporté.")
    return content, media_type


def _entry_lock(entry_id: int) -> threading.Lock:
    with _lock_guard:
        lock = _entry_locks.get(entry_id)
        if lock is None:
            lock = threading.Lock()
            _entry_locks[entry_id] = lock
        return lock


def _fail_path(dest: Path) -> Path:
    return dest.with_suffix(".fail")


def _meta_path(dest: Path) -> Path:
    return dest.with_suffix(".meta")


def _fail_active(fail: Path, moment: datetime) -> bool:
    if not fail.is_file():
        return False
    try:
        expires = datetime.fromisoformat(fail.read_text(encoding="utf-8").strip())
    except ValueError:
        fail.unlink(missing_ok=True)
        return False
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= moment:
        fail.unlink(missing_ok=True)
        return False
    return True


def _remember_fail(fail: Path, moment: datetime, *, code: str) -> None:
    ttl = _TRANSIENT_FAIL_TTL if code in _TRANSIENT_CODES else _FAIL_TTL
    expires = moment + ttl
    tmp = fail.with_suffix(".fail.tmp")
    tmp.write_text(expires.isoformat(), encoding="utf-8")
    os.replace(tmp, fail)


def _write_cache_atomic(dest: Path, meta: Path, body: bytes, media_type: str) -> None:
    digest = hashlib.sha256(body).hexdigest()[:16]
    etag = f'"{digest}"'
    tmp_body = dest.with_suffix(".bin.tmp")
    tmp_meta = meta.with_suffix(".meta.tmp")
    tmp_body.write_bytes(body)
    tmp_meta.write_text(f"{media_type}\n{etag}\n", encoding="utf-8")
    os.replace(tmp_body, dest)
    os.replace(tmp_meta, meta)


def _read_cached(dest: Path, meta: Path) -> tuple[Path, str, str] | None:
    if not dest.is_file() or not meta.is_file():
        return None
    lines = meta.read_text(encoding="utf-8").splitlines()
    media_type = lines[0].strip() if lines else "image/jpeg"
    etag = lines[1].strip() if len(lines) > 1 else etag_for(dest)
    return dest, media_type, etag


def resolve_cached_source_poster(
    db: Session,
    settings: Settings,
    row: CatalogEntry,
) -> tuple[Path, str, str] | None:
    """Return cached file path, media type and etag; fetch once if needed."""
    absolute = normalize_source_poster_url(db, settings, row, row.source_poster_url)
    if not absolute:
        return None
    allowed = allowed_poster_hosts(db, settings, row.source)
    dest = cache_path(settings, row.id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    meta = _meta_path(dest)
    fail = _fail_path(dest)
    moment = datetime.now(timezone.utc)
    cached = _read_cached(dest, meta)
    if cached is not None:
        return cached
    if _fail_active(fail, moment):
        return None
    lock = _entry_lock(row.id)
    if not lock.acquire(blocking=False):
        if not lock.acquire(timeout=_LOCK_WAIT_SECONDS):
            cached = _read_cached(dest, meta)
            return cached
    try:
        cached = _read_cached(dest, meta)
        if cached is not None:
            return cached
        if _fail_active(fail, moment):
            return None
        try:
            body, media_type = fetch_source_poster(
                settings,
                absolute,
                allowed_hosts=allowed,
                headers=_poster_fetch_headers(db, settings, row),
            )
        except SourcePosterError as exc:
            _remember_fail(fail, moment, code=exc.code)
            logger.info(
                "Affiche source non récupérée pour l'entrée %s (%s).",
                row.id,
                exc.code,
            )
            return None
        _write_cache_atomic(dest, meta, body, media_type)
        return _read_cached(dest, meta)
    finally:
        lock.release()


def etag_for(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f'"{digest}"'

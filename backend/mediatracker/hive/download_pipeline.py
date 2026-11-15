"""LOT3 : reprise, renouvellement grant, intégrité et durcissement aria2 Hive."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Download, DownloadHiveFetch
from mediatracker.hive.download_aria2 import (
    aria2_headers_for_ctx,
    ensure_mint_for_download,
    hive_file_download_url,
)
from mediatracker.hive.fetch_grant import HIVE_FETCH_GRANT_HEADER
from mediatracker.hive.peer_outbound import validate_hive_peer_base_url
from mediatracker.hive.source_catalog import HiveCatalogMatch
from mediatracker.hive.transfer_settings import transfer_recv_bps
from mediatracker.providers.downloaders.aria2 import Aria2Error

logger = logging.getLogger(__name__)

GRANT_RENEW_LEAD = timedelta(minutes=30)
HIVE_ARIA2_EXTRA_OPTIONS: dict[str, str] = {
    "continue": "true",
    "follow-redirect": "false",
    "max-uri-redirection": "0",
}
_GRANT_HEADER_LINE = re.compile(
    rf"^\s*header\s*=\s*{re.escape(HIVE_FETCH_GRANT_HEADER)}\s*:\s*.+$",
    re.IGNORECASE,
)
_AUTH_FAILURE_HINTS = ("401", "403", "unauthorized", "forbidden", "grant", "fetch-grant")
_SIZE_MISMATCH = "HIVE_SIZE_MISMATCH"
_SOURCE_CHANGED = "HIVE_SOURCE_CHANGED"
_SHA_MISMATCH = "HIVE_SHA_MISMATCH"
HIVE_PEER_BUSY_MESSAGE = "Source Hive occupée."
HIVE_PEER_BUSY_BACKOFFS = (
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=5),
)
HIVE_PEER_BUSY_MARK_DEFAULT = timedelta(seconds=30)
HIVE_MAX_SOURCE_SWITCHES = 8
_peer_busy_strikes: dict[int, int] = {}
_peer_busy_until: dict[tuple[int, str], datetime] = {}
_hive_failover_switches: dict[int, int] = {}


def hive_peer_busy_key(peer_base_url: str) -> str:
    return (peer_base_url or "").strip().rstrip("/").casefold()


def mark_hive_peer_busy(
    download_id: int | None,
    peer_key: str,
    now: datetime,
    *,
    retry_after: timedelta | None = None,
) -> None:
    if download_id is None or not peer_key:
        return
    delay = retry_after if retry_after is not None else HIVE_PEER_BUSY_MARK_DEFAULT
    if delay < timedelta(0):
        delay = HIVE_PEER_BUSY_MARK_DEFAULT
    cap = timedelta(minutes=5)
    if delay > cap:
        delay = cap
    _peer_busy_until[(int(download_id), peer_key)] = _aware_utc(now) + delay


def hive_peer_marked_busy(download_id: int | None, peer_key: str, now: datetime) -> bool:
    if download_id is None or not peer_key:
        return False
    key = (int(download_id), peer_key)
    until = _peer_busy_until.get(key)
    if until is None:
        return False
    if _aware_utc(now) >= until:
        _peer_busy_until.pop(key, None)
        return False
    return True


def hive_failover_switch_cap_reached(download_id: int | None) -> bool:
    if download_id is None:
        return True
    return _hive_failover_switches.get(int(download_id), 0) >= HIVE_MAX_SOURCE_SWITCHES


def record_hive_failover_switch(download_id: int | None) -> None:
    if download_id is None:
        return
    did = int(download_id)
    _hive_failover_switches[did] = min(_hive_failover_switches.get(did, 0) + 1, HIVE_MAX_SOURCE_SWITCHES)


def schedule_hive_immediate_retry(row: Download, now: datetime, *, clear_handle: bool = True) -> None:
    moment = _aware_utc(now)
    row.state = "pending"
    row.error_code = None
    row.error = None
    row.next_retry_at = moment
    if clear_handle:
        row.handle = None
        row.attempt_started_at = None


def clear_hive_peer_busy_strike(download_id: int | None) -> None:
    if download_id is not None:
        did = int(download_id)
        _peer_busy_strikes.pop(did, None)
        _hive_failover_switches.pop(did, None)
        for key in list(_peer_busy_until):
            if key[0] == did:
                del _peer_busy_until[key]


def defer_hive_peer_busy(row: Download, now: datetime, *, clear_handle: bool = True) -> None:
    """File d'attente Hive occupée : pending court, sans consommer attempts ni enterrer de lien."""
    moment = _aware_utc(now)
    download_id = row.id
    if row.error_code == "HIVE_PEER_BUSY" and download_id is not None:
        strike = min(_peer_busy_strikes.get(int(download_id), 0) + 1, len(HIVE_PEER_BUSY_BACKOFFS) - 1)
    else:
        strike = 0
    if download_id is not None:
        _peer_busy_strikes[int(download_id)] = strike
    row.state = "pending"
    row.error_code = "HIVE_PEER_BUSY"
    row.error = HIVE_PEER_BUSY_MESSAGE
    row.next_retry_at = moment + HIVE_PEER_BUSY_BACKOFFS[strike]
    if clear_handle:
        row.handle = None
        row.attempt_started_at = None


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def hive_fetch_ctx(db: Session, row: Download) -> DownloadHiveFetch | None:
    if row.id is None:
        return None
    return db.get(DownloadHiveFetch, row.id)


def is_hive_fetch_download(db: Session, settings: Settings | None, row: Download) -> bool:
    del settings
    return hive_fetch_ctx(db, row) is not None


def hive_catalog_drift(ctx: DownloadHiveFetch, match: HiveCatalogMatch) -> str | None:
    """True si le catalogue ne correspond plus à la source figée (reprise interdite)."""
    needle = str(match.sha256_hex).lower()
    if ctx.sha256_hex.strip().lower() != needle:
        return "empreinte catalogue différente"
    if int(ctx.expected_size) != int(match.expected_size):
        return "taille attendue différente"
    return None


def hive_resume_allowed(
    db: Session,
    ctx: DownloadHiveFetch,
    match: HiveCatalogMatch,
    *,
    partial_path: Path | None,
) -> tuple[bool, str]:
    reason = hive_catalog_drift(ctx, match)
    if reason is not None:
        return False, reason
    if partial_path is not None and partial_path.is_file():
        on_disk = partial_path.stat().st_size
        expected = int(ctx.expected_size)
        if on_disk > expected:
            return False, "fichier partiel plus grand que la taille attendue"
    return True, ""


def hive_recv_limit_option(db: Session) -> str:
    """Valeur aria2 max-download-limit (0 = illimité)."""
    return str(transfer_recv_bps(db))


def hive_aria2_options(db: Session) -> dict[str, str]:
    opts = dict(HIVE_ARIA2_EXTRA_OPTIONS)
    opts["max-download-limit"] = hive_recv_limit_option(db)
    return opts


def sync_hive_recv_rate_limits(db: Session, downloader) -> int:
    from mediatracker.core.pipeline.constants import ACTIVE
    from mediatracker.db.models import Download
    from mediatracker.providers.downloaders.aria2 import Aria2Error

    limit = hive_recv_limit_option(db)
    change = getattr(downloader, "change_option", None)
    if change is None:
        return 0
    count = 0
    for row in db.scalars(select(Download).where(Download.state.in_(ACTIVE))).all():
        if not is_hive_fetch_download(db, None, row) or not row.handle:
            continue
        try:
            change(row.handle, {"max-download-limit": limit})
            count += 1
        except Aria2Error:
            continue
    return count


def validate_hive_download_url(peer_base_url: str, file_id: bytes) -> str:
    url = hive_file_download_url(peer_base_url, file_id)
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    base_parts = urlsplit(peer_base_url.strip().rstrip("/") + "/")
    base_host = (base_parts.hostname or "").lower()
    if host != base_host:
        raise ValueError("URL Hive incohérente avec le pair.")
    validate_hive_peer_base_url(peer_base_url)
    return url


def grant_expiring_soon(ctx: DownloadHiveFetch, now: datetime) -> bool:
    exp = ctx.grant_expires_at
    if exp is None:
        return True
    return _aware_utc(exp) - _aware_utc(now) <= GRANT_RENEW_LEAD


def mint_headers_for_row(
    db: Session, settings: Settings, row: Download, *, now: datetime, force: bool = False
) -> tuple[DownloadHiveFetch, dict[str, str]] | None:
    ctx = hive_fetch_ctx(db, row)
    if ctx is None:
        return None
    if not force and not grant_expiring_soon(ctx, now):
        from mediatracker.hive.download_aria2 import _read_grant

        secret = _read_grant(ctx, settings)
        if secret and ctx.grant_expires_at and _aware_utc(ctx.grant_expires_at) > _aware_utc(now):
            return ctx, aria2_headers_for_ctx(ctx, secret)
    grant = ensure_mint_for_download(db, settings, ctx, now=now, force=force or grant_expiring_soon(ctx, now))
    return ctx, aria2_headers_for_ctx(ctx, grant.grant_secret)


def scrub_hive_grant_from_session_text(text: str) -> tuple[str, int]:
    removed = 0
    out_lines: list[str] = []
    for line in text.splitlines():
        if _GRANT_HEADER_LINE.match(line):
            removed += 1
            continue
        out_lines.append(line)
    body = "\n".join(out_lines)
    if body and not body.endswith("\n"):
        body += "\n"
    return body, removed


def scrub_hive_grant_from_session_file(session: Path) -> int:
    if not session.is_file():
        return 0
    try:
        text = session.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    cleaned, removed = scrub_hive_grant_from_session_text(text)
    if not removed:
        return 0
    try:
        session.write_text(cleaned, encoding="utf-8")
    except OSError as exc:
        logger.info("Session aria2 non assainie : %s", exc)
        return 0
    return removed


def maybe_refresh_hive_grant(
    db: Session,
    settings: Settings,
    row: Download,
    downloader,
    *,
    now: datetime,
    force: bool = False,
) -> bool:
    """Renouvelle le grant si besoin et pousse les en-têtes via changeOption (sans logger le secret)."""
    if not is_hive_fetch_download(db, settings, row) or not row.handle:
        return False
    packed = mint_headers_for_row(db, settings, row, now=now, force=force)
    if packed is None:
        return False
    _ctx, headers = packed
    client = getattr(downloader, "client", downloader)
    change = getattr(client, "change_option", None)
    if change is None:
        return False
    try:
        change(
            row.handle,
            {
                "header": [f"{key}: {value}" for key, value in headers.items() if value],
                "max-download-limit": hive_recv_limit_option(db),
            },
        )
    except Aria2Error as exc:
        logger.info("Renouvellement grant Hive ignoré pour %s : %s", row.id, exc.message)
        return False
    save = getattr(client, "save_session", None)
    if save is not None:
        try:
            save()
        except Aria2Error:
            pass
    scrub_hive_grant_from_session_file(Path(settings.config_dir) / "aria2.session")
    return True


def hive_auth_failure_message(error: str | None) -> bool:
    if not error:
        return False
    lowered = error.casefold()
    return any(hint in lowered for hint in _AUTH_FAILURE_HINTS)


def hive_peer_rate_failure(error: str | None) -> bool:
    if not error:
        return False
    text = error.casefold()
    if "429" in text or "too many requests" in text:
        return True
    if "peer_rate" in text or "peer rate" in text:
        return True
    if "source hive occup" in text:
        return True
    return False


def hive_source_transient_failure(error: str | None) -> bool:
    """Panne transitoire de l'instance source (pas une erreur de grant)."""
    if not error or hive_auth_failure_message(error):
        return False
    text = error.casefold()
    if re.search(r"\b5\d{2}\b", text):
        return True
    if "timeout" in text or "timed out" in text:
        return True
    if "connection refused" in text or "connexion refusée" in text:
        return True
    return False


def hive_source_unreachable_message(error: str | None) -> str:
    text = (error or "").strip()
    suffix = ""
    match = re.search(r"\b(5\d{2})\b", text)
    if match:
        suffix = f" (erreur {match.group(1)})"
    return (
        f"L'instance source Hive ne répond pas{suffix}. "
        "Nouvel essai prévu, le fichier partiel est conservé."
    )


def sync_hive_grants_for_active(
    db: Session, settings: Settings, downloader, *, now: datetime
) -> int:
    from mediatracker.core.pipeline.constants import ACTIVE

    count = 0
    rows = db.scalars(select(Download).where(Download.state.in_(ACTIVE))).all()
    for row in rows:
        if not row.handle:
            continue
        if maybe_refresh_hive_grant(db, settings, row, downloader, now=now, force=False):
            count += 1
    return count


def verify_hive_completed_file(
    db: Session,
    settings: Settings,
    row: Download,
    path: Path,
) -> tuple[str | None, str]:
    """Contrôle taille obligatoire, SHA catalogue optionnel. Ne supprime jamais le fichier."""
    ctx = hive_fetch_ctx(db, row)
    if ctx is None:
        return None, ""
    if not path.is_file():
        return "FILE_MISSING", "Fichier téléchargé introuvable."
    actual = path.stat().st_size
    expected = int(ctx.expected_size)
    if actual != expected:
        return (
            _SIZE_MISMATCH,
            f"Taille incorrecte ({actual} octets, attendu {expected}). Le fichier partiel est conservé.",
        )
    catalog_sha = (ctx.content_sha256_hex or "").strip().lower()
    if not catalog_sha:
        return None, ""
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        got = digest.hexdigest()
    except OSError:
        return "HIVE_SHA_READ", "Impossible de lire le fichier pour contrôle d'empreinte."
    if got.lower() != catalog_sha:
        return (
            _SHA_MISMATCH,
            "Empreinte catalogue différente (corruption même taille non détectable autrement). "
            "Fichier conservé.",
        )
    return None, ""

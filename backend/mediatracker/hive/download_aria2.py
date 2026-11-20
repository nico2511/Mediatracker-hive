"""LOT2 : Hive → Download aria2 via fetch-grant (sans second moteur)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.download_guard import DOWNLOAD_TERMINAL_STATES
from mediatracker.db.models import Download, DownloadHiveFetch, Media, MediaItem
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.hive.fetch_grant import (
    GRANT_RENEW_LEAD,
    HIVE_FETCH_GRANT_HEADER,
    MintedFetchGrant,
)
from mediatracker.hive.identity import (
    load_private_key,
)
from mediatracker.hive.peer_auth import HIVE_RUCHE_HEADER, sign_request_headers
from mediatracker.hive.peer_member import member_base_url
from mediatracker.hive.peer_outbound import validate_hive_peer_base_url
from mediatracker.hive.peer_transport import peer_request
from mediatracker.hive.source_catalog import HiveCatalogMatch
from mediatracker.hive.transfer_sources import rank_sources
from mediatracker.security.crypto import decrypt_secret, encrypt_secret

HIVE_PEER_BUSY_MESSAGE = "Source Hive occupée."
logger = logging.getLogger(__name__)


def _normalize_peer_base(url: str) -> str:
    return (url or "").strip().rstrip("/")


def active_hive_fetches_for_peer(
    db: Session, peer_base_url: str, *, exclude_download_id: int | None = None
) -> int:
    """Téléchargements Hive actifs depuis la même instance pair (côté demandeur)."""
    from mediatracker.core.pipeline.constants import ACTIVE

    peer = _normalize_peer_base(peer_base_url)
    if not peer:
        return 0
    query = (
        select(func.count())
        .select_from(DownloadHiveFetch)
        .join(Download, Download.id == DownloadHiveFetch.download_id)
        .where(
            Download.state.in_(ACTIVE),
            DownloadHiveFetch.peer_base_url == peer,
        )
    )
    if exclude_download_id is not None:
        query = query.where(Download.id != int(exclude_download_id))
    return int(db.scalar(query) or 0)


class HiveGrantMintError(Exception):
    def __init__(self, code: str, message: str = "", *, retry_after_seconds: float | None = None) -> None:
        self.code = code
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message or code)


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _source_at(sources: list[dict[str, Any]], index: int) -> dict[str, Any] | None:
    if index < 0 or index >= len(sources):
        return None
    src = sources[index]
    return src if isinstance(src, dict) else None


def _ranked_sources_for_ctx(db: Session, ctx: DownloadHiveFetch) -> list[dict[str, Any]]:
    try:
        raw = json.loads(ctx.sources_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    return rank_sources(db, [s for s in raw if isinstance(s, dict)])


def hive_source_identity(source: dict[str, Any]) -> tuple[str, str] | None:
    origin = str(source.get("origine") or "").strip()
    file_hex = str(source.get("file_id_hex") or "").strip().lower()
    if not origin or not file_hex:
        return None
    return origin, file_hex


def hive_ctx_source_identity(ctx: DownloadHiveFetch) -> tuple[str, str]:
    return ctx.origin_instance_id.strip(), ctx.file_id.hex().lower()


def hive_source_index_for_identity(ranked: list[dict[str, Any]], identity: tuple[str, str]) -> int | None:
    for index, source in enumerate(ranked):
        ident = hive_source_identity(source)
        if ident == identity:
            return index
    return None


def current_ranked_source_index(ranked: list[dict[str, Any]], ctx: DownloadHiveFetch) -> int:
    found = hive_source_index_for_identity(ranked, hive_ctx_source_identity(ctx))
    if found is not None:
        return found
    if not ranked:
        return 0
    return max(0, min(int(ctx.active_source_index), len(ranked) - 1))


def _ring_indices_from(rank_len: int, cur: int) -> list[int]:
    """Anneau inclusif : source courante en premier."""
    if rank_len <= 0:
        return []
    return [(cur + i) % rank_len for i in range(rank_len)]


def _ring_indices_after(rank_len: int, cur: int) -> list[int]:
    """Anneau à partir de la source suivante (failover après occupé/429)."""
    if rank_len <= 0:
        return []
    return [(cur + 1 + i) % rank_len for i in range(rank_len)]


@dataclass(frozen=True)
class _HiveFetchCtxSnapshot:
    active_source_index: int
    ruche_id: bytes
    file_id: bytes
    origin_instance_id: str
    peer_base_url: str
    grant_ciphertext: str | None
    grant_expires_at: datetime | None
    updated_at: datetime | None


def _snapshot_hive_fetch_ctx(ctx: DownloadHiveFetch) -> _HiveFetchCtxSnapshot:
    return _HiveFetchCtxSnapshot(
        active_source_index=int(ctx.active_source_index),
        ruche_id=bytes(ctx.ruche_id),
        file_id=bytes(ctx.file_id),
        origin_instance_id=str(ctx.origin_instance_id or ""),
        peer_base_url=str(ctx.peer_base_url or ""),
        grant_ciphertext=ctx.grant_ciphertext,
        grant_expires_at=ctx.grant_expires_at,
        updated_at=ctx.updated_at,
    )


def _restore_hive_fetch_ctx(ctx: DownloadHiveFetch, snap: _HiveFetchCtxSnapshot) -> None:
    ctx.active_source_index = snap.active_source_index
    ctx.ruche_id = snap.ruche_id
    ctx.file_id = snap.file_id
    ctx.origin_instance_id = snap.origin_instance_id
    ctx.peer_base_url = snap.peer_base_url
    ctx.grant_ciphertext = snap.grant_ciphertext
    ctx.grant_expires_at = snap.grant_expires_at
    ctx.updated_at = snap.updated_at


def _peer_unusable_for_job(
    db: Session,
    row: Download,
    peer_base_url: str,
    now: datetime,
    *,
    limit: int,
) -> bool:
    from mediatracker.hive.download_pipeline import (
        hive_peer_busy_key,
        hive_peer_marked_busy,
        mark_hive_peer_busy,
    )

    if not peer_base_url:
        return True
    peer_key = hive_peer_busy_key(peer_base_url)
    if hive_peer_marked_busy(row.id, peer_key, now):
        return True
    if active_hive_fetches_for_peer(db, peer_base_url, exclude_download_id=row.id) >= limit:
        mark_hive_peer_busy(row.id, peer_key, now)
        return True
    return False


def apply_hive_fetch_source(
    db: Session,
    ctx: DownloadHiveFetch,
    source: dict[str, Any],
    *,
    index: int,
    now: datetime,
) -> bool:
    resolved = _resolve_active_source(db, source)
    if resolved is None:
        return False
    ruche_id, file_id, origin, peer_base = resolved
    moment = _aware_utc(now)
    ctx.active_source_index = int(index)
    ctx.ruche_id = ruche_id
    ctx.file_id = file_id
    ctx.origin_instance_id = origin
    ctx.peer_base_url = peer_base
    ctx.grant_ciphertext = None
    ctx.grant_expires_at = None
    ctx.updated_at = moment
    return True


def hive_failover_after_peer_busy(
    db: Session,
    row: Download,
    ctx: DownloadHiveFetch,
    now: datetime,
    *,
    retry_after: timedelta | None = None,
) -> bool:
    """Bascule en anneau sur les sources classées ; False si aucune libre (ctx restauré)."""
    from mediatracker.hive.download_pipeline import (
        hive_failover_switch_cap_reached,
        hive_peer_busy_key,
        mark_hive_peer_busy,
        record_hive_failover_switch,
    )
    from mediatracker.hive.transfer_settings import transfer_parallel_limit

    moment = _aware_utc(now)
    snap = _snapshot_hive_fetch_ctx(ctx)
    ranked = _ranked_sources_for_ctx(db, ctx)
    if len(ranked) <= 1:
        if ctx.peer_base_url:
            mark_hive_peer_busy(
                row.id,
                hive_peer_busy_key(ctx.peer_base_url),
                moment,
                retry_after=retry_after,
            )
        return False
    if hive_failover_switch_cap_reached(row.id):
        if ctx.peer_base_url:
            mark_hive_peer_busy(
                row.id,
                hive_peer_busy_key(ctx.peer_base_url),
                moment,
                retry_after=retry_after,
            )
        return False
    if ctx.peer_base_url:
        mark_hive_peer_busy(
            row.id,
            hive_peer_busy_key(ctx.peer_base_url),
            moment,
            retry_after=retry_after,
        )
    cur = current_ranked_source_index(ranked, ctx)
    limit = transfer_parallel_limit(db)
    for index in _ring_indices_after(len(ranked), cur):
        if hive_failover_switch_cap_reached(row.id):
            _restore_hive_fetch_ctx(ctx, snap)
            return False
        source = ranked[index]
        resolved = _resolve_active_source(db, source)
        if resolved is None:
            continue
        _, _, _, peer_base = resolved
        if _peer_unusable_for_job(db, row, peer_base, moment, limit=limit):
            continue
        if not apply_hive_fetch_source(db, ctx, source, index=index, now=moment):
            continue
        if index != cur:
            record_hive_failover_switch(row.id)
        return True
    _restore_hive_fetch_ctx(ctx, snap)
    return False


def _resolve_active_source(db: Session, src: dict[str, Any]) -> tuple[bytes, bytes, str, str] | None:
    try:
        ruche_id = bytes.fromhex(str(src["ruche_id_hex"]))
        file_id = bytes.fromhex(str(src["file_id_hex"]))
    except (ValueError, TypeError):
        return None
    origin = str(src.get("origine") or "").strip()
    if not origin:
        return None
    base = member_base_url(db, ruche_id, origin)
    if not base:
        return None
    try:
        peer_base = validate_hive_peer_base_url(base)
    except Exception:
        return None
    return ruche_id, file_id, origin, peer_base


def hive_download_active_for_item(db: Session, item: MediaItem, sha256_hex: str) -> bool:
    needle = str(sha256_hex).lower()
    rows = list(
        db.scalars(
            select(DownloadHiveFetch)
            .join(Download, Download.id == DownloadHiveFetch.download_id)
            .where(
                DownloadHiveFetch.sha256_hex == needle,
                Download.media_item_id == item.id,
                Download.state.not_in(tuple(DOWNLOAD_TERMINAL_STATES)),
            )
        ).all()
    )
    return bool(rows)


def hive_reception_active_for_item(db: Session, item: MediaItem, sha256_hex: str, settings: Settings) -> bool:
    del settings
    return hive_download_active_for_item(db, item, sha256_hex)


def _store_grant(row: DownloadHiveFetch, grant: MintedFetchGrant, settings: Settings) -> None:
    row.grant_ciphertext = encrypt_secret(grant.grant_secret, settings.secret_key)
    row.grant_expires_at = _aware_utc(grant.expires_at)


def _read_grant(row: DownloadHiveFetch, settings: Settings) -> str | None:
    raw = row.grant_ciphertext
    if not raw:
        return None
    try:
        return decrypt_secret(raw, settings.secret_key).strip()
    except Exception:
        return None


def mint_remote_fetch_grant(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    file_id: bytes,
    origin_instance_id: str,
    peer_base_url: str,
    now: datetime,
) -> MintedFetchGrant:
    pk = load_private_key(settings)
    if pk is None:
        raise HiveGrantMintError("no_identity", "Identité Hive absente.")
    path = f"/hive/ruche/{ruche_id.hex()}/files/{file_id.hex()}/fetch-grant"
    headers = sign_request_headers(
        private_key=pk,
        method="POST",
        path=path,
        query="",
        ruche_id=ruche_id,
        body=b"",
    )
    release_sqlite_before_network(db)
    resp = peer_request(
        base_url=peer_base_url,
        method="POST",
        url_path=path,
        headers=headers,
        body=b"",
    )
    if resp.status_code != 200:
        code = {401: "peer_auth", 404: "peer_file", 429: "peer_rate"}.get(resp.status_code, "peer_mint")
        retry_after_seconds = None
        if resp.status_code == 429:
            from mediatracker.hive.peer_rate_limit import parse_retry_after_header

            retry_after_seconds = parse_retry_after_header(resp.headers.get("Retry-After"))
        raise HiveGrantMintError(
            code,
            f"Mint grant refusé ({resp.status_code}).",
            retry_after_seconds=retry_after_seconds,
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise HiveGrantMintError("peer_mint", "Réponse mint illisible.") from exc
    grant = body.get("grant") if isinstance(body, dict) else None
    expires_raw = body.get("expires_at") if isinstance(body, dict) else None
    if not isinstance(grant, str) or not grant.strip():
        raise HiveGrantMintError("peer_mint", "Grant absent dans la réponse.")
    expires_at = _aware_utc(now)
    if isinstance(expires_raw, str) and expires_raw.strip():
        try:
            expires_at = datetime.fromisoformat(expires_raw.replace("Z", "+00:00"))
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    size_raw = body.get("content_size") if isinstance(body, dict) else None
    mtime_raw = body.get("content_mtime_ns") if isinstance(body, dict) else None
    content_size: int | None = None
    content_mtime_ns: int | None = None
    if isinstance(size_raw, int):
        content_size = int(size_raw)
    elif isinstance(size_raw, str) and size_raw.strip().isdigit():
        content_size = int(size_raw.strip())
    if isinstance(mtime_raw, int):
        content_mtime_ns = int(mtime_raw)
    elif isinstance(mtime_raw, str) and mtime_raw.strip().isdigit():
        content_mtime_ns = int(mtime_raw.strip())
    return MintedFetchGrant(
        grant_secret=str(grant).strip(),
        expires_at=expires_at,
        content_size=content_size,
        content_mtime_ns=content_mtime_ns,
    )


def hive_file_download_url(peer_base_url: str, file_id: bytes) -> str:
    base = peer_base_url.rstrip("/")
    return f"{base}/hive/files/{file_id.hex()}"


def attach_or_refresh_hive_fetch(
    db: Session,
    settings: Settings,
    *,
    download: Download,
    match: HiveCatalogMatch,
    now: datetime,
    requested_version_key: str | None = None,
) -> DownloadHiveFetch | None:
    moment = _aware_utc(now)
    sources = rank_sources(db, list(match.sources))
    if not sources:
        return None
    ctx = db.get(DownloadHiveFetch, download.id)
    existing = ctx is not None and bool((ctx.sources_json or "").strip())
    if existing:
        from mediatracker.hive.download_pipeline import hive_catalog_drift

        if hive_catalog_drift(ctx, match):
            return None
        index = current_ranked_source_index(sources, ctx)
        active = sources[index]
        resolved = _resolve_active_source(db, active)
        if resolved is None:
            return None
    else:
        index = 0
        resolved = _resolve_active_source(db, sources[0])
        if resolved is None:
            return None
    ruche_id, file_id, origin, peer_base = resolved
    if ctx is None:
        ctx = DownloadHiveFetch(
            download_id=download.id,
            created_at=moment,
        )
        db.add(ctx)
    ctx.sha256_hex = str(match.sha256_hex).lower()
    ctx.sources_json = json.dumps(sources, separators=(",", ":"))
    ctx.active_source_index = index
    ctx.ruche_id = ruche_id
    ctx.file_id = file_id
    ctx.origin_instance_id = origin
    ctx.peer_base_url = peer_base
    ctx.expected_size = int(match.expected_size)
    ctx.content_sha256_hex = str(match.sha256_hex).lower() or None
    if requested_version_key:
        ctx.requested_version_key = str(requested_version_key).strip()[:80] or None
    elif not existing:
        ctx.requested_version_key = None
    if not existing:
        ctx.content_mtime_ns = 0
    ctx.updated_at = moment
    return ctx


def enqueue_hive_download_match(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    match: HiveCatalogMatch,
    *,
    now: datetime | None = None,
    requested_version_key: str | None = None,
) -> Download | None:
    """Crée ou réutilise un Download pending avec contexte Hive (pas de HiveTransferJob)."""
    moment = _aware_utc(now or datetime.now(timezone.utc))
    if item.id is None:
        return None
    existing = db.scalar(
        select(Download)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(
            Download.media_item_id == item.id,
            DownloadHiveFetch.sha256_hex == str(match.sha256_hex).lower(),
            Download.state.not_in(tuple(DOWNLOAD_TERMINAL_STATES)),
        )
        .limit(1)
    )
    if existing is not None:
        item.source_available = True
        if item.state == "missing":
            item.state = "available"
        return existing
    row = Download(media_item_id=item.id, state="pending", created_at=moment)
    db.add(row)
    db.flush()
    if (
        attach_or_refresh_hive_fetch(
            db,
            settings,
            download=row,
            match=match,
            now=moment,
            requested_version_key=requested_version_key,
        )
        is None
    ):
        db.delete(row)
        db.flush()
        return None
    item.source_available = True
    if item.state == "missing":
        item.state = "available"
    return row


def ensure_mint_for_download(
    db: Session,
    settings: Settings,
    ctx: DownloadHiveFetch,
    *,
    now: datetime,
    force: bool = False,
) -> MintedFetchGrant:
    moment = _aware_utc(now)
    renew = force
    if not renew and ctx.grant_expires_at is not None:
        expires = _aware_utc(ctx.grant_expires_at)
        if expires > moment:
            if expires - moment > GRANT_RENEW_LEAD:
                secret = _read_grant(ctx, settings)
                if secret:
                    return MintedFetchGrant(grant_secret=secret, expires_at=expires)
            else:
                renew = True
        else:
            renew = True
    elif not renew and ctx.grant_expires_at is None:
        renew = True
    if not renew:
        secret = _read_grant(ctx, settings)
        if secret and ctx.grant_expires_at and _aware_utc(ctx.grant_expires_at) > moment:
            return MintedFetchGrant(grant_secret=secret, expires_at=_aware_utc(ctx.grant_expires_at))
    minted = mint_remote_fetch_grant(
        db,
        settings,
        ruche_id=ctx.ruche_id,
        file_id=ctx.file_id,
        origin_instance_id=ctx.origin_instance_id,
        peer_base_url=ctx.peer_base_url,
        now=moment,
    )
    _store_grant(ctx, minted, settings)
    if minted.content_mtime_ns is not None and int(minted.content_mtime_ns) > 0:
        ctx.content_mtime_ns = int(minted.content_mtime_ns)
    if minted.content_size is not None and int(minted.content_size) > 0 and int(ctx.expected_size) == 0:
        ctx.expected_size = int(minted.content_size)
    ctx.updated_at = moment
    return minted


def aria2_headers_for_ctx(ctx: DownloadHiveFetch, grant_secret: str) -> dict[str, str]:
    return {
        HIVE_RUCHE_HEADER: ctx.ruche_id.hex(),
        HIVE_FETCH_GRANT_HEADER: grant_secret,
    }


def aria2_headers_for_download(
    db: Session, settings: Settings, row: Download, *, now: datetime
) -> dict[str, str] | None:
    ctx = db.get(DownloadHiveFetch, row.id)
    if ctx is None:
        return None
    grant = ensure_mint_for_download(db, settings, ctx, now=now)
    return aria2_headers_for_ctx(ctx, grant.grant_secret)


def hive_suggested_filename(media: Media, item: MediaItem, match: HiveCatalogMatch) -> str:
    cle = match.cle if isinstance(match.cle, dict) else {}
    raw = cle.get("nom") or cle.get("filename")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:500]
    if media.type == "series" and item.season is not None and item.episode is not None:
        return f"{media.title} S{item.season:02d}E{item.episode:02d}.mkv"[:500]
    return f"{media.title}.mkv"[:500]


def prepare_hive_aria2_unlock(
    db: Session,
    settings: Settings,
    *,
    row: Download,
    item: MediaItem,
    media: Media,
    now: datetime,
    defer_final: bool,
):
    """Mint grant + URL aria2 pour un Download Hive. Retourne le tuple unlock ou un code pipeline."""
    from mediatracker.core.pipeline.jobs import _fail, _throttle_poll
    from mediatracker.core.pipeline.unlock import _journal_source_attempt
    from mediatracker.hive.download_pipeline import defer_hive_peer_busy
    from mediatracker.hive.source_download import resolve_hive_match_for_item
    from mediatracker.hive.transfer_settings import transfer_parallel_limit

    resolved = resolve_hive_match_for_item(db, settings, media=media, item=item, now=now, row=row)
    outcome = resolved.outcome
    if outcome == "profile_missing":
        _journal_source_attempt(
            db, row, media, item, "hive", issue="refused", reason="Profil du sondage introuvable.", now=now
        )
        if defer_final:
            return None
        _fail(db, row, "POLL_PROFILE_MISSING", "Profil du sondage introuvable.", now)
        return "waiting"
    if outcome == "miss":
        _journal_source_attempt(db, row, media, item, "hive", issue="empty", now=now)
        return None
    if outcome == "pending":
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            "hive",
            issue="empty",
            reason="Indisponible dans la ruche pour l'instant.",
            now=now,
        )
        if defer_final:
            row.error_code = row.error_code or "HIVE_UNAVAILABLE"
            row.error = row.error or "Indisponible dans la ruche pour l'instant."
            return None
        _fail(db, row, "HIVE_UNAVAILABLE", "Indisponible dans la ruche pour l'instant.", now)
        _throttle_poll(db, media, settings, now)
        return "waiting"
    if outcome != "queued" or resolved.match is None:
        return None

    match = resolved.match
    prior = db.get(DownloadHiveFetch, row.id)
    ctx = attach_or_refresh_hive_fetch(db, settings, download=row, match=match, now=now)
    if ctx is None:
        from mediatracker.hive.download_pipeline import hive_catalog_drift

        if prior is not None and hive_catalog_drift(prior, match):
            message = (
                "La source Hive a changé (taille ou version catalogue). "
                "Reprise interdite ; le fichier partiel est conservé."
            )
            _journal_source_attempt(db, row, media, item, "hive", issue="error", reason=message, now=now)
            if defer_final:
                row.error_code = "HIVE_SOURCE_CHANGED"
                row.error = message
                return None
            _fail(db, row, "HIVE_SOURCE_CHANGED", message, now)
            return "waiting"
        _journal_source_attempt(db, row, media, item, "hive", issue="empty", now=now)
        return None

    from datetime import timedelta

    from mediatracker.hive.download_pipeline import (
        hive_failover_switch_cap_reached,
        hive_peer_busy_key,
        mark_hive_peer_busy,
        record_hive_failover_switch,
    )

    ranked = _ranked_sources_for_ctx(db, ctx)
    limit = transfer_parallel_limit(db)
    grant = None
    ranked_snap: _HiveFetchCtxSnapshot | None = None
    if not ranked:
        peer_key = hive_peer_busy_key(ctx.peer_base_url)
        peer_full = (
            ctx.peer_base_url
            and active_hive_fetches_for_peer(db, ctx.peer_base_url, exclude_download_id=row.id) >= limit
        )
        if peer_full:
            _journal_source_attempt(
                db,
                row,
                media,
                item,
                "hive",
                issue="empty",
                reason=HIVE_PEER_BUSY_MESSAGE,
                now=now,
            )
            defer_hive_peer_busy(row, now)
            return "waiting"
        try:
            grant = ensure_mint_for_download(db, settings, ctx, now=now)
        except HiveGrantMintError as exc:
            _journal_source_attempt(db, row, media, item, "hive", issue="error", reason=exc.message, now=now)
            if exc.code == "peer_rate":
                retry_after = None
                if exc.retry_after_seconds is not None:
                    retry_after = timedelta(seconds=float(exc.retry_after_seconds))
                mark_hive_peer_busy(row.id, peer_key, now, retry_after=retry_after)
                defer_hive_peer_busy(row, now)
                return "waiting"
            if defer_final:
                return None
            _fail(db, row, "HIVE_GRANT", exc.message or "Grant Hive refusé.", now)
            return "waiting"
    else:
        ranked_snap = _snapshot_hive_fetch_ctx(ctx)
        cur = current_ranked_source_index(ranked, ctx)
        for index in _ring_indices_from(len(ranked), cur):
            if hive_failover_switch_cap_reached(row.id):
                break
            source = ranked[index]
            resolved = _resolve_active_source(db, source)
            if resolved is None:
                continue
            _, _, _, peer_base = resolved
            if _peer_unusable_for_job(db, row, peer_base, now, limit=limit):
                _journal_source_attempt(
                    db,
                    row,
                    media,
                    item,
                    "hive",
                    issue="empty",
                    reason=HIVE_PEER_BUSY_MESSAGE,
                    now=now,
                )
                continue
            if not apply_hive_fetch_source(db, ctx, source, index=index, now=now):
                continue
            try:
                grant = ensure_mint_for_download(db, settings, ctx, now=now)
                if index != cur:
                    record_hive_failover_switch(row.id)
                break
            except HiveGrantMintError as exc:
                _journal_source_attempt(db, row, media, item, "hive", issue="error", reason=exc.message, now=now)
                if exc.code == "peer_rate":
                    retry_after = None
                    if exc.retry_after_seconds is not None:
                        retry_after = timedelta(seconds=float(exc.retry_after_seconds))
                    peer_key = hive_peer_busy_key(peer_base)
                    mark_hive_peer_busy(row.id, peer_key, now, retry_after=retry_after)
                    continue
                _restore_hive_fetch_ctx(ctx, ranked_snap)
                if defer_final:
                    return None
                _fail(db, row, "HIVE_GRANT", exc.message or "Grant Hive refusé.", now)
                return "waiting"

    if grant is None and ranked:
        if ranked_snap is not None:
            _restore_hive_fetch_ctx(ctx, ranked_snap)
        _journal_source_attempt(
            db,
            row,
            media,
            item,
            "hive",
            issue="empty",
            reason=HIVE_PEER_BUSY_MESSAGE,
            now=now,
        )
        defer_hive_peer_busy(row, now)
        return "waiting"

    from mediatracker.hive.download_pipeline import validate_hive_download_url

    try:
        direct_url = validate_hive_download_url(ctx.peer_base_url, ctx.file_id)
    except (ValueError, Exception):
        _journal_source_attempt(db, row, media, item, "hive", issue="error", reason="URL pair Hive refusée.", now=now)
        if defer_final:
            return None
        _fail(db, row, "HIVE_URL", "URL pair Hive refusée.", now)
        return "waiting"
    filename = hive_suggested_filename(media, item, match)
    size = int(ctx.expected_size)
    _journal_source_attempt(db, row, media, item, "hive", issue="found", link_count=1, now=now)
    row.size = size
    headers = aria2_headers_for_ctx(ctx, grant.grant_secret)
    return (None, direct_url, filename, size, None, headers)

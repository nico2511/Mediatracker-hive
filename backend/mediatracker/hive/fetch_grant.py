"""Jeton d'accès fichier temporaire (mint après auth pair, GET via en-tête)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveFetchGrant
from mediatracker.hive.access import is_active_member
from mediatracker.hive.file_serve import LocalFileBinding, resolve_local_file
from mediatracker.hive.share_exclusions import list_excluded_ruche_ids
from mediatracker.hive.share_policy import item_wants_hive_share

HIVE_FETCH_GRANT_HEADER = "Hive-Fetch-Grant"

DEFAULT_GRANT_TTL = timedelta(hours=48)
GRANT_RENEW_LEAD = timedelta(minutes=30)
MAX_ACTIVE_GRANTS_PER_REQUESTER = 32


class FetchGrantDenied(Exception):
    """Grant absent, expiré ou contexte invalide → 401."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class FetchGrantContext:
    requester_instance_id: str
    grant_id: int


@dataclass(frozen=True)
class MintedFetchGrant:
    grant_secret: str
    expires_at: datetime
    content_size: int | None = None
    content_mtime_ns: int | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hash_grant_secret(secret: str) -> str:
    return hashlib.sha256(secret.strip().encode("utf-8")).hexdigest()


def _grant_file_mtime_ns(binding: LocalFileBinding) -> int:
    return int(binding.mtime_ns)


def file_share_allows_ruche(db: Session, *, media_item_id: int, ruche_id: bytes) -> bool:
    if not item_wants_hive_share(db, media_item_id):
        return False
    exclude_all, excluded = list_excluded_ruche_ids(db, media_item_id)
    if exclude_all:
        return False
    return ruche_id not in excluded


def _binding_matches_grant(binding: LocalFileBinding, row: HiveFetchGrant) -> bool:
    if binding.size != int(row.content_size):
        return False
    if _grant_file_mtime_ns(binding) != int(row.content_mtime_ns):
        return False
    bound_sha = (row.content_sha256_hex or "").strip().lower()
    if bound_sha and binding.sha256_hex.strip().lower() != bound_sha:
        return False
    return True


def purge_expired_grants(
    db: Session,
    *,
    now: datetime,
    ruche_id: bytes | None = None,
    requester_id: str | None = None,
) -> int:
    moment = _aware_utc(now)
    query = select(HiveFetchGrant)
    if ruche_id is not None:
        query = query.where(HiveFetchGrant.ruche_id == ruche_id)
    if requester_id is not None:
        query = query.where(HiveFetchGrant.requester_instance_id == requester_id)
    removed = 0
    for row in list(db.scalars(query).all()):
        if _aware_utc(row.expires_at) < moment:
            db.delete(row)
            removed += 1
    if removed:
        db.flush()
    return removed


def _active_grant_rows(
    db: Session,
    *,
    ruche_id: bytes,
    requester_id: str,
    for_update: bool = False,
):
    query = select(HiveFetchGrant).where(
        HiveFetchGrant.ruche_id == ruche_id,
        HiveFetchGrant.requester_instance_id == requester_id,
        HiveFetchGrant.revoked_at.is_(None),
    )
    if for_update:
        query = query.with_for_update()
    return db.scalars(query).all()


def _count_active_grants(db: Session, *, ruche_id: bytes, requester_id: str, now: datetime) -> int:
    moment = _aware_utc(now)
    rows = _active_grant_rows(db, ruche_id=ruche_id, requester_id=requester_id)
    return sum(1 for row in rows if _aware_utc(row.expires_at) > moment)


def _count_active_grants_locked(db: Session, *, ruche_id: bytes, requester_id: str, now: datetime) -> int:
    moment = _aware_utc(now)
    rows = _active_grant_rows(db, ruche_id=ruche_id, requester_id=requester_id, for_update=True)
    return sum(1 for row in rows if _aware_utc(row.expires_at) > moment)


def mint_fetch_grant(
    db: Session,
    settings,
    *,
    ruche_id: bytes,
    file_id: bytes,
    requester_id: str,
    now: datetime,
    ttl: timedelta = DEFAULT_GRANT_TTL,
) -> MintedFetchGrant:
    purge_expired_grants(db, now=now, ruche_id=ruche_id, requester_id=requester_id)
    if not is_active_member(db, ruche_id=ruche_id, instance_id=requester_id):
        raise FetchGrantDenied("member")
    binding = resolve_local_file(db, settings, ruche_id=ruche_id, file_id=file_id)
    if binding is None:
        raise FetchGrantDenied("file")
    if not file_share_allows_ruche(db, media_item_id=binding.media_item_id, ruche_id=ruche_id):
        raise FetchGrantDenied("share")
    active = _count_active_grants_locked(db, ruche_id=ruche_id, requester_id=requester_id, now=now)
    if active >= MAX_ACTIVE_GRANTS_PER_REQUESTER:
        raise FetchGrantDenied("cap")
    secret = secrets.token_urlsafe(32)
    expires_at = now + ttl
    row = HiveFetchGrant(
        grant_sha256_hex=_hash_grant_secret(secret),
        ruche_id=ruche_id,
        file_id=file_id,
        requester_instance_id=requester_id,
        media_item_id=binding.media_item_id,
        content_size=binding.size,
        content_mtime_ns=_grant_file_mtime_ns(binding),
        content_sha256_hex=binding.sha256_hex,
        created_at=now,
        expires_at=expires_at,
    )
    db.add(row)
    db.flush()
    over_cap = (
        _count_active_grants_locked(db, ruche_id=ruche_id, requester_id=requester_id, now=now)
        > MAX_ACTIVE_GRANTS_PER_REQUESTER
    )
    if over_cap:
        db.delete(row)
        db.flush()
        raise FetchGrantDenied("cap")
    return MintedFetchGrant(grant_secret=secret, expires_at=expires_at)


def revoke_active_fetch_grants_for_item(db: Session, *, media_item_id: int, now: datetime) -> int:
    """Marque révoqués les grants encore actifs liés à cet épisode (fichier retiré ou déplacé)."""
    moment = _aware_utc(now)
    rows = list(
        db.scalars(
            select(HiveFetchGrant).where(
                HiveFetchGrant.media_item_id == media_item_id,
                HiveFetchGrant.revoked_at.is_(None),
            )
        ).all()
    )
    for row in rows:
        row.revoked_at = moment
    if rows:
        db.flush()
    return len(rows)


def verify_fetch_grant(
    db: Session,
    settings,
    *,
    grant_secret: str,
    ruche_id: bytes,
    file_id: bytes,
    now: datetime,
) -> FetchGrantContext:
    if not grant_secret or not grant_secret.strip():
        raise FetchGrantDenied("missing")
    digest = _hash_grant_secret(grant_secret)
    row = db.scalar(select(HiveFetchGrant).where(HiveFetchGrant.grant_sha256_hex == digest).limit(1))
    if row is None:
        raise FetchGrantDenied("unknown")
    if row.revoked_at is not None:
        raise FetchGrantDenied("revoked")
    if _aware_utc(row.expires_at) <= _aware_utc(now):
        raise FetchGrantDenied("expired")
    if row.ruche_id != ruche_id or row.file_id != file_id:
        raise FetchGrantDenied("scope")
    if not is_active_member(db, ruche_id=ruche_id, instance_id=row.requester_instance_id):
        row.revoked_at = now
        db.flush()
        raise FetchGrantDenied("member")
    if not file_share_allows_ruche(db, media_item_id=int(row.media_item_id), ruche_id=ruche_id):
        row.revoked_at = now
        db.flush()
        raise FetchGrantDenied("share")
    binding = resolve_local_file(db, settings, ruche_id=ruche_id, file_id=file_id)
    if binding is None or int(binding.media_item_id) != int(row.media_item_id):
        row.revoked_at = now
        db.flush()
        raise FetchGrantDenied("file")
    if not _binding_matches_grant(binding, row):
        row.revoked_at = now
        db.flush()
        raise FetchGrantDenied("version")
    return FetchGrantContext(requester_instance_id=row.requester_instance_id, grant_id=int(row.id))

"""File SQLite des empreintes Hive (ordre stable, dédoublonnage à l'enfilage)."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveFileFingerprint, HiveFingerprintQueue, HiveShareIntent
from mediatracker.hive.fingerprint import HIVE_CLAIM_STALE

logger = logging.getLogger(__name__)

FAIL_REASON_TRASH = "Fichier en corbeille."
FAILED_RETRY_MIN_AGE = timedelta(hours=24)


def _failed_eligible_for_daily_retry(row: HiveFingerprintQueue, *, now: datetime) -> bool:
    if row.status != "failed":
        return False
    if (row.fail_reason or "").strip() == FAIL_REASON_TRASH:
        return False
    updated = row.updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    elif updated.tzinfo != timezone.utc:
        updated = updated.astimezone(timezone.utc)
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    if moment.tzinfo != timezone.utc:
        moment = moment.astimezone(timezone.utc)
    return moment - updated >= FAILED_RETRY_MIN_AGE


def enqueue_fingerprint_work(db: Session, media_item_id: int, *, now: datetime) -> None:
    """Enfile un calcul sans doubler l'ordre FIFO existant."""
    row = db.scalar(
        select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == media_item_id)
    )
    if row is None:
        db.add(
            HiveFingerprintQueue(
                media_item_id=media_item_id,
                status="pending",
                created_at=now,
                updated_at=now,
            )
        )
        db.flush()
        return
    if row.status == "failed":
        row.status = "pending"
        row.fail_reason = None
        row.claimed_at = None
        row.updated_at = now
        db.flush()
    elif row.status == "running":
        pass
    elif row.status == "pending":
        row.updated_at = now
        db.flush()


def dequeue_completed(db: Session, media_item_id: int) -> None:
    db.execute(delete(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == media_item_id))


def release_running_to_pending(db: Session, media_item_id: int, *, now: datetime) -> None:
    """Erreur transitoire ou Hive OFF : libère le claim sans marquer failed."""
    row = db.scalar(
        select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == media_item_id)
    )
    if row is None:
        return
    if row.status == "running":
        row.status = "pending"
        row.claimed_at = None
        row.updated_at = now
        db.flush()


def drop_queue_for_media_items(db: Session, media_item_ids: set[int]) -> int:
    if not media_item_ids:
        return 0
    result = db.execute(
        delete(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id.in_(media_item_ids))
    )
    changed = int(result.rowcount or 0)
    if changed:
        db.flush()
    return changed


def abandon_orphan_queue_work(db: Session, *, now: datetime) -> set[int]:
    """Retire de la file les épisodes sans intention de partage (miroir abandon FP)."""
    from mediatracker.hive.share_policy import hive_catalog_auto_share_enabled, item_wants_hive_share

    if hive_catalog_auto_share_enabled(db):
        return set()
    removed: set[int] = set()
    rows = list(db.scalars(select(HiveFingerprintQueue)).all())
    for row in rows:
        media_item_id = int(row.media_item_id)
        if item_wants_hive_share(db, media_item_id):
            continue
        row.updated_at = now
        db.delete(row)
        removed.add(media_item_id)
    if removed:
        db.flush()
    return removed


def mark_queue_failed(db: Session, media_item_id: int, *, reason: str, now: datetime) -> None:
    row = db.scalar(
        select(HiveFingerprintQueue).where(HiveFingerprintQueue.media_item_id == media_item_id)
    )
    if row is None:
        return
    row.status = "failed"
    row.fail_reason = reason
    row.claimed_at = None
    row.updated_at = now
    db.flush()


def queue_has_work(db: Session) -> bool:
    return db.scalar(
        select(HiveFingerprintQueue.id)
        .where(HiveFingerprintQueue.status.in_(("pending", "running")))
        .limit(1)
    ) is not None


def refresh_running_claim_lease(db: Session, media_item_id: int, *, now: datetime) -> bool:
    """Rafraîchit ``claimed_at`` pour un job ``running`` encore tenu en mémoire par le daemon."""
    row = db.scalar(
        select(HiveFingerprintQueue).where(
            HiveFingerprintQueue.media_item_id == media_item_id,
            HiveFingerprintQueue.status == "running",
        )
    )
    if row is None:
        return False
    row.claimed_at = now
    row.updated_at = now
    db.flush()
    return True


def recover_fingerprint_queue(db: Session, *, now: datetime) -> int:
    """Claims abandonnés et lignes orphelines (empreinte déjà complète)."""
    changed = 0
    stale_before = (now - HIVE_CLAIM_STALE).timestamp()
    rows = list(db.scalars(select(HiveFingerprintQueue)).all())
    for row in rows:
        fp = db.get(HiveFileFingerprint, row.media_item_id)
        if fp is not None and (fp.sha256_hex or "").strip():
            db.delete(row)
            changed += 1
            continue
        if row.status != "running" or row.claimed_at is None:
            continue
        claimed = row.claimed_at
        if claimed.tzinfo is None:
            claimed = claimed.replace(tzinfo=timezone.utc)
        if claimed.timestamp() < stale_before:
            row.status = "pending"
            row.claimed_at = None
            row.updated_at = now
            changed += 1
    if changed:
        db.flush()
    return changed


def backfill_queue_from_incomplete(db: Session, *, now: datetime) -> int:
    """Après crash : ré-enfile les empreintes incomplètes avec intention de partage."""
    from mediatracker.hive.identity import hive_enabled_in_db

    if not hive_enabled_in_db(db):
        return 0
    added = 0
    rows = list(
        db.scalars(
            select(HiveFileFingerprint.media_item_id)
            .join(HiveShareIntent, HiveShareIntent.media_item_id == HiveFileFingerprint.media_item_id)
            .where(HiveFileFingerprint.sha256_hex.is_(None))
        ).all()
    )
    for media_item_id in rows:
        existing = db.scalar(
            select(HiveFingerprintQueue).where(
                HiveFingerprintQueue.media_item_id == int(media_item_id)
            )
        )
        if existing is not None:
            if _failed_eligible_for_daily_retry(existing, now=now):
                enqueue_fingerprint_work(db, int(media_item_id), now=now)
                added += 1
            continue
        enqueue_fingerprint_work(db, int(media_item_id), now=now)
        added += 1
    return added


def claim_next_pending(db: Session, *, now: datetime) -> int | None:
    """Un seul consommateur : passe la plus ancienne entrée ``pending`` en ``running``."""
    row = db.scalar(
        select(HiveFingerprintQueue)
        .where(HiveFingerprintQueue.status == "pending")
        .order_by(HiveFingerprintQueue.id.asc())
        .limit(1)
    )
    if row is None:
        return None
    result = db.execute(
        update(HiveFingerprintQueue)
        .where(
            HiveFingerprintQueue.id == row.id,
            HiveFingerprintQueue.status == "pending",
        )
        .values(status="running", claimed_at=now, updated_at=now)
    )
    if int(result.rowcount or 0) != 1:
        return None
    db.flush()
    return int(row.media_item_id)


def count_pending_ordered(db: Session) -> list[int]:
    return list(
        db.scalars(
            select(HiveFingerprintQueue.media_item_id)
            .where(HiveFingerprintQueue.status == "pending")
            .order_by(HiveFingerprintQueue.id.asc())
        ).all()
    )


def queue_depth_by_status(db: Session) -> dict[str, int]:
    out: dict[str, int] = {}
    for status, n in db.execute(
        select(HiveFingerprintQueue.status, func.count())
        .group_by(HiveFingerprintQueue.status)
    ).all():
        out[str(status)] = int(n or 0)
    return out

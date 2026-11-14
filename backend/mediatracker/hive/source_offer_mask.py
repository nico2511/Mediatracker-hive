"""Masque local des offres refusées par une source (404 / mismatch), sans effacer le gossip."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveEntry, HiveSourceOfferMask


def _latest_catalog_seq_for_file(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    file_id: bytes,
) -> int:
    value = db.scalar(
        select(func.max(HiveEntry.seq)).where(
            HiveEntry.ruche_id == ruche_id,
            HiveEntry.origin_instance_id == origin_instance_id,
            HiveEntry.file_id == file_id,
        )
    )
    return int(value or 0)


def _mask_key(ruche_id: bytes, origin_id: str, file_id: bytes) -> tuple[bytes, str, bytes]:
    return ruche_id, origin_id, file_id


def mask_source_offer(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    file_id: bytes,
    sha256_hex: str | None,
    reason: str,
    now: datetime,
) -> None:
    row = db.get(HiveSourceOfferMask, _mask_key(ruche_id, origin_instance_id, file_id))
    masked_origin_seq = _latest_catalog_seq_for_file(
        db, ruche_id=ruche_id, origin_instance_id=origin_instance_id, file_id=file_id
    )
    if row is None:
        db.add(
            HiveSourceOfferMask(
                ruche_id=ruche_id,
                origin_instance_id=origin_instance_id,
                file_id=file_id,
                sha256_hex=sha256_hex,
                reason=reason,
                masked_at=now,
                masked_origin_seq=masked_origin_seq,
            )
        )
    else:
        row.sha256_hex = sha256_hex
        row.reason = reason
        row.masked_at = now
        row.masked_origin_seq = masked_origin_seq
    db.flush()


def is_source_offer_masked(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    file_id: bytes | None,
) -> bool:
    if file_id is None:
        return False
    return db.get(HiveSourceOfferMask, _mask_key(ruche_id, origin_instance_id, file_id)) is not None


def masked_source_keys(db: Session) -> set[tuple[bytes, str, bytes]]:
    rows = db.scalars(select(HiveSourceOfferMask)).all()
    return {(r.ruche_id, r.origin_instance_id, r.file_id) for r in rows}


def clear_mask_for_offer(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    file_id: bytes,
) -> None:
    db.execute(
        delete(HiveSourceOfferMask).where(
            HiveSourceOfferMask.ruche_id == ruche_id,
            HiveSourceOfferMask.origin_instance_id == origin_instance_id,
            HiveSourceOfferMask.file_id == file_id,
        )
    )
    db.flush()


def maybe_lift_source_offer_mask(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    file_id: bytes,
    sha256_hex: str | None,
    seq: int,
) -> bool:
    """Lève un masque 404 si la source republie une offre plausible plus récente."""
    row = db.get(HiveSourceOfferMask, _mask_key(ruche_id, origin_instance_id, file_id))
    if row is None:
        return False
    new_sha = (sha256_hex or "").strip().lower()
    old_sha = (row.sha256_hex or "").strip().lower()
    lift_by_sha = bool(new_sha and old_sha and new_sha != old_sha)
    lift_by_seq = int(seq) > int(row.masked_origin_seq or 0)
    if lift_by_sha or lift_by_seq:
        clear_mask_for_offer(
            db, ruche_id=ruche_id, origin_instance_id=origin_instance_id, file_id=file_id
        )
        return True
    return False


def purge_masks_superseded_by_tombstones(db: Session) -> int:
    """Retire les masques dont la dernière entrée gossip pour la même offre est une pierre tombale."""
    masks = list(db.scalars(select(HiveSourceOfferMask)).all())
    if not masks:
        return 0
    removed = 0
    for mask in masks:
        latest = db.scalar(
            select(HiveEntry)
            .where(
                HiveEntry.ruche_id == mask.ruche_id,
                HiveEntry.origin_instance_id == mask.origin_instance_id,
                HiveEntry.file_id == mask.file_id,
            )
            .order_by(HiveEntry.seq.desc())
            .limit(1)
        )
        if latest is not None and latest.etat == "retire":
            db.delete(mask)
            removed += 1
    return removed

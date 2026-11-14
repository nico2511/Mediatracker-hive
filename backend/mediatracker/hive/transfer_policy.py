"""Quota, créneaux et éligibilité d'envoi (T5)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveEntry, HiveMember, HiveReceiveQuota, HiveRuche
from mediatracker.hive.identity import HIVE_PUBLIC_ADDRESS_KEY, _read_setting_text  # noqa: SLF001
from mediatracker.hive.transfer_settings import (
    DEFAULT_WEEKLY_QUOTA,
    in_transfer_window,
    week_start_date,
)


class TransferQuotaExceeded(Exception):
    """Quota hebdomadaire dépassé pour un non-partageur."""


class TransferWindowClosed(Exception):
    """Créneau de transfert fermé localement."""


def local_can_send(db: Session) -> bool:
    """Mode tirage : pas d'adresse publique → ne sert aucun fichier."""
    return bool(_read_setting_text(db, HIVE_PUBLIC_ADDRESS_KEY).strip())


def member_public_address(db: Session, *, ruche_id: bytes, instance_id: str) -> str:
    row = db.scalar(
        select(HiveMember.public_address)
        .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == instance_id)
        .limit(1)
    )
    return str(row or "").strip()


def member_is_public_peer(db: Session, *, ruche_id: bytes, instance_id: str) -> bool:
    addr = member_public_address(db, ruche_id=ruche_id, instance_id=instance_id)
    return bool(addr) and addr != "tirage"


def member_shares_in_ruche(db: Session, *, ruche_id: bytes, instance_id: str) -> bool:
    if not member_is_public_peer(db, ruche_id=ruche_id, instance_id=instance_id):
        return False
    return (
        db.scalar(
            select(HiveEntry.id)
            .where(
                HiveEntry.ruche_id == ruche_id,
                HiveEntry.origin_instance_id == instance_id,
                HiveEntry.etat == "disponible",
            )
            .limit(1)
        )
        is not None
    )


def weekly_quota_for_ruche(db: Session, ruche_id: bytes) -> int:
    row = db.get(HiveRuche, ruche_id)
    if row is not None and row.weekly_quota_bytes is not None:
        return int(row.weekly_quota_bytes)
    return DEFAULT_WEEKLY_QUOTA


def _quota_row(db: Session, *, ruche_id: bytes, member_id: str, when: datetime) -> HiveReceiveQuota:
    ws = week_start_date(when)
    row = db.get(HiveReceiveQuota, (ruche_id, member_id, ws))
    if row is None:
        row = HiveReceiveQuota(ruche_id=ruche_id, member_instance_id=member_id, week_start=ws, bytes_used=0)
        db.add(row)
        db.flush()
    return row


def check_inbound_fetch(
    db: Session,
    *,
    ruche_id: bytes,
    requester_id: str,
    content_length: int,
    now: datetime,
) -> None:
    """Contrôles côté envoyeur avant de servir un morceau."""
    if not in_transfer_window(db, now):
        raise TransferWindowClosed()
    if not member_shares_in_ruche(db, ruche_id=ruche_id, instance_id=requester_id):
        used = _quota_row(db, ruche_id=ruche_id, member_id=requester_id, when=now)
        limit = weekly_quota_for_ruche(db, ruche_id)
        if used.bytes_used + max(0, content_length) > limit:
            raise TransferQuotaExceeded()


def record_inbound_bytes(
    db: Session,
    *,
    ruche_id: bytes,
    requester_id: str,
    nbytes: int,
    now: datetime,
) -> None:
    if member_shares_in_ruche(db, ruche_id=ruche_id, instance_id=requester_id):
        return
    row = _quota_row(db, ruche_id=ruche_id, member_id=requester_id, when=now)
    row.bytes_used = int(row.bytes_used) + max(0, nbytes)

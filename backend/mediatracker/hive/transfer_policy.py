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


def reset_member_receive_quota(
    db: Session,
    *,
    ruche_id: bytes,
    member_id: str,
    now: datetime,
) -> int:
    """Remet à zéro le compteur local de la semaine en cours pour un membre non-partageur."""
    if (
        db.scalar(
            select(HiveMember.member_instance_id)
            .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == member_id)
            .limit(1)
        )
        is None
    ):
        raise ValueError("member_not_found")
    if member_shares_in_ruche(db, ruche_id=ruche_id, instance_id=member_id):
        raise ValueError("shares_in_ruche")
    ws = week_start_date(now)
    row = db.get(HiveReceiveQuota, (ruche_id, member_id, ws))
    previous = int(row.bytes_used) if row is not None else 0
    row = _quota_row(db, ruche_id=ruche_id, member_id=member_id, when=now)
    row.bytes_used = 0
    db.flush()
    return previous


def member_receive_quota_snapshot(
    db: Session,
    *,
    ruche_id: bytes,
    instance_id: str,
    now: datetime,
) -> dict[str, int | str | bool | None]:
    """Consommation locale (envoyeur) pour l'écran Membres."""
    from mediatracker.hive.transfer_settings import quota_usage_status, week_start_date

    shares = member_shares_in_ruche(db, ruche_id=ruche_id, instance_id=instance_id)
    if shares:
        return {
            "shares_in_ruche": True,
            "receive_bytes_used": None,
            "receive_quota_bytes": None,
            "receive_quota_status": None,
        }
    limit = weekly_quota_for_ruche(db, ruche_id)
    ws = week_start_date(now)
    row = db.get(HiveReceiveQuota, (ruche_id, instance_id, ws))
    used = int(row.bytes_used) if row is not None else 0
    return {
        "shares_in_ruche": False,
        "receive_bytes_used": used,
        "receive_quota_bytes": limit,
        "receive_quota_status": quota_usage_status(used, limit),
    }

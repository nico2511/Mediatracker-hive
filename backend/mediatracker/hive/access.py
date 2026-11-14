"""Contrôle d'accès membre par ruche (T3, utilisé avant routes /hive/ T4)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveMember, HiveRucheExcluded


def is_active_member(db: Session, *, ruche_id: bytes, instance_id: str) -> bool:
    if (
        db.scalar(
            select(HiveRucheExcluded.id)
            .where(HiveRucheExcluded.ruche_id == ruche_id, HiveRucheExcluded.instance_id == instance_id)
            .limit(1)
        )
        is not None
    ):
        return False
    return (
        db.scalar(
            select(HiveMember.id)
            .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == instance_id)
            .limit(1)
        )
        is not None
    )


def assert_member_allowed(db: Session, *, ruche_id: bytes, instance_id: str) -> None:
    if not is_active_member(db, ruche_id=ruche_id, instance_id=instance_id):
        raise PermissionError("member_not_allowed")

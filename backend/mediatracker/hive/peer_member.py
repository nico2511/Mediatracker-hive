"""Résolution d’adresse publique d’un membre Hive (pair source)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveMember


def member_base_url(db: Session, ruche_id: bytes, origin_id: str) -> str | None:
    addr = db.scalar(
        select(HiveMember.public_address)
        .where(HiveMember.ruche_id == ruche_id, HiveMember.member_instance_id == origin_id)
        .limit(1)
    )
    text = str(addr or "").strip().rstrip("/")
    if not text or text == "tirage":
        return None
    return text

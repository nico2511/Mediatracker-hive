"""Journal Hive : remise à zéro du quota hebdomadaire d'un membre."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.events import record_event

_TYPE_RESET = "hive.quota.reset"


def record_member_quota_reset(
    db: Session,
    *,
    ruche_id_hex: str,
    member_instance_id: str,
    member_display: str,
    when: datetime,
) -> None:
    label = member_display.strip() or member_instance_id[:8]
    record_event(
        db,
        _TYPE_RESET,
        level="info",
        payload={
            "message": f"Quota hebdomadaire remis à zéro pour {label} (ruche {ruche_id_hex[:8]}…).",
            "ruche_id_hex": ruche_id_hex,
            "member_instance_id": member_instance_id,
        },
        when=when,
    )
"""Mesure et persistance du débit pair (octets / durée monotone)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.db.models import HiveOriginReachability
from mediatracker.hive.peer_gossip import record_reachability


def record_transfer_sample(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    nbytes: int,
    duration_seconds: float,
    now: datetime,
) -> None:
    """Enregistre le débit du dernier morceau réussi ; ignore échecs et durées nulles."""
    if nbytes <= 0 or duration_seconds <= 0:
        return
    bps = float(nbytes) / float(duration_seconds)
    if bps <= 0:
        return
    record_reachability(db, ruche_id=ruche_id, origin_id=origin_instance_id, now=now)
    db.flush()
    row = db.get(HiveOriginReachability, (ruche_id, origin_instance_id))
    if row is None:
        db.add(
            HiveOriginReachability(
                ruche_id=ruche_id,
                origin_instance_id=origin_instance_id,
                last_ok_at=now,
                measured_bps=bps,
                measured_at=now,
            )
        )
        return
    row.measured_bps = bps
    row.measured_at = now


def measured_bps_for_origin(db: Session, *, ruche_id: bytes, origin_instance_id: str) -> float | None:
    row = db.get(HiveOriginReachability, (ruche_id, origin_instance_id))
    if row is None or row.measured_bps is None or row.measured_bps <= 0:
        return None
    return float(row.measured_bps)

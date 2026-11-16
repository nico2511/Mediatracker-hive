"""Instantané de charge pair (transferts sortants) : gossip, stockage, lecture."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import HivePeerLoad
from mediatracker.hive.transfer_bandwidth import TransferBandwidthLimiter
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.hive.transfer_policy import local_can_send
from mediatracker.hive.transfer_settings import in_transfer_window, transfer_parallel_limit, transfer_send_bps

LOAD_STALE_SECONDS = 600.0
LOAD_MAX_FUTURE_SECONDS = 300.0
_MAX_PARALLEL = 16
_MAX_BPS = 10_000_000_000.0

_parallel_gate: TransferParallelGate | None = None
_send_bandwidth: TransferBandwidthLimiter | None = None


def bind_transfer_load_runtime(
    *,
    parallel_gate: TransferParallelGate | None,
    send_bandwidth: TransferBandwidthLimiter | None,
) -> None:
    """Rattache les compteurs processus (API) pour l'instantané local."""
    global _parallel_gate, _send_bandwidth
    _parallel_gate = parallel_gate
    _send_bandwidth = send_bandwidth


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_snapshot_at(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return _aware_utc(parsed)


def _clamp_int(value: object, *, lo: int, hi: int) -> int | None:
    if not isinstance(value, int):
        return None
    return max(lo, min(value, hi))


def _clamp_bps(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if f < 0 or f > _MAX_BPS:
        return None
    return f


def validate_load_payload(raw: object) -> dict[str, Any] | None:
    """Valide un objet ``load`` gossip ; retourne un dict normalisé ou None."""
    if not isinstance(raw, dict):
        return None
    out_active = _clamp_int(raw.get("out_active"), lo=0, hi=_MAX_PARALLEL)
    parallel_limit = _clamp_int(raw.get("parallel_limit"), lo=1, hi=_MAX_PARALLEL)
    send_cap_bps = _clamp_int(raw.get("send_cap_bps"), lo=0, hi=int(_MAX_BPS))
    if out_active is None or parallel_limit is None or send_cap_bps is None:
        return None
    busy = raw.get("busy")
    if not isinstance(busy, bool):
        return None
    snapshot_at = _parse_snapshot_at(raw.get("at"))
    if snapshot_at is None:
        return None
    send_avail = _clamp_bps(raw.get("send_avail_bps"))
    if raw.get("send_avail_bps") is not None and send_avail is None:
        return None
    return {
        "out_active": out_active,
        "parallel_limit": parallel_limit,
        "send_cap_bps": send_cap_bps,
        "send_avail_bps": send_avail,
        "busy": busy,
        "at": snapshot_at,
    }


def load_is_stale(received_at: datetime, now: datetime) -> bool:
    received = _aware_utc(received_at)
    moment = _aware_utc(now)
    return (moment - received).total_seconds() > LOAD_STALE_SECONDS


def _estimate_send_avail_bps(db: Session, *, send_cap: int, limiter: TransferBandwidthLimiter | None) -> float | None:
    del db
    if send_cap <= 0:
        return None
    if limiter is None:
        return None
    outbound = limiter.estimated_outbound_bps()
    if outbound is None:
        return None
    return max(0.0, float(send_cap) - float(outbound))


def _local_out_active(parallel_gate: TransferParallelGate | None) -> int:
    if parallel_gate is None:
        return 0
    return max(0, parallel_gate.active_count())


def local_peer_busy(db: Session, now: datetime, *, parallel_gate: TransferParallelGate | None) -> bool:
    if not local_can_send(db):
        return True
    if not in_transfer_window(db, now):
        return True
    limit = transfer_parallel_limit(db)
    if parallel_gate is not None and parallel_gate.active_count() >= limit:
        return True
    return False


def build_local_load_snapshot(
    db: Session,
    now: datetime,
    *,
    parallel_gate: TransferParallelGate | None = None,
    send_bandwidth: TransferBandwidthLimiter | None = None,
) -> dict[str, Any] | None:
    """Instantané publiable en gossip ; None si aucun gate API (ex. worker)."""
    gate = parallel_gate if parallel_gate is not None else _parallel_gate
    if gate is None:
        return None
    limiter = send_bandwidth if send_bandwidth is not None else _send_bandwidth
    moment = _aware_utc(now)
    parallel_limit = transfer_parallel_limit(db)
    send_cap = transfer_send_bps(db)
    out_active = min(_local_out_active(gate), parallel_limit)
    busy = local_peer_busy(db, moment, parallel_gate=gate)
    payload: dict[str, Any] = {
        "out_active": out_active,
        "parallel_limit": parallel_limit,
        "send_cap_bps": send_cap,
        "send_avail_bps": _estimate_send_avail_bps(db, send_cap=send_cap, limiter=limiter),
        "busy": busy,
        "at": moment.isoformat().replace("+00:00", "Z"),
    }
    return payload


def ingest_peer_load(
    db: Session,
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    load: object,
    now: datetime,
) -> bool:
    normalized = validate_load_payload(load)
    if normalized is None:
        return False
    moment = _aware_utc(now)
    snapshot_at = _aware_utc(normalized["at"])
    if (snapshot_at - moment).total_seconds() > LOAD_MAX_FUTURE_SECONDS:
        return False
    key = (ruche_id, origin_instance_id)
    row = db.get(HivePeerLoad, key)
    if row is not None and snapshot_at <= _aware_utc(row.snapshot_at):
        return False
    if row is None:
        row = HivePeerLoad(
            ruche_id=ruche_id,
            origin_instance_id=origin_instance_id,
            out_active=normalized["out_active"],
            parallel_limit=normalized["parallel_limit"],
            send_cap_bps=normalized["send_cap_bps"],
            send_avail_bps=normalized["send_avail_bps"],
            busy=normalized["busy"],
            snapshot_at=normalized["at"],
            received_at=moment,
        )
        db.add(row)
    else:
        row.out_active = normalized["out_active"]
        row.parallel_limit = normalized["parallel_limit"]
        row.send_cap_bps = normalized["send_cap_bps"]
        row.send_avail_bps = normalized["send_avail_bps"]
        row.busy = normalized["busy"]
        row.snapshot_at = normalized["at"]
        row.received_at = moment
    return True


@dataclass(frozen=True)
class PeerLoadView:
    ruche_id: bytes
    origin_instance_id: str
    out_active: int
    parallel_limit: int
    send_cap_bps: int
    send_avail_bps: float | None
    busy: bool
    snapshot_at: datetime
    received_at: datetime
    stale: bool

    def free_capacity_ratio(self) -> float:
        if self.parallel_limit <= 0:
            return 0.0
        free = max(0, self.parallel_limit - self.out_active)
        return free / float(self.parallel_limit)


def peer_load_row(
    db: Session, *, ruche_id: bytes, origin_instance_id: str, now: datetime
) -> PeerLoadView | None:
    row = db.get(HivePeerLoad, (ruche_id, origin_instance_id))
    if row is None:
        return None
    stale = load_is_stale(row.received_at, now)
    return PeerLoadView(
        ruche_id=ruche_id,
        origin_instance_id=origin_instance_id,
        out_active=int(row.out_active),
        parallel_limit=int(row.parallel_limit),
        send_cap_bps=int(row.send_cap_bps),
        send_avail_bps=float(row.send_avail_bps) if row.send_avail_bps is not None else None,
        busy=bool(row.busy),
        snapshot_at=row.snapshot_at,
        received_at=row.received_at,
        stale=stale,
    )


def list_peer_loads_for_ruches(db: Session, ruche_ids: list[bytes], now: datetime) -> list[PeerLoadView]:
    if not ruche_ids:
        return []
    rows = list(
        db.scalars(select(HivePeerLoad).where(HivePeerLoad.ruche_id.in_(ruche_ids)).order_by(HivePeerLoad.received_at.desc()))
        .all()
    )
    out: list[PeerLoadView] = []
    for row in rows:
        view = peer_load_row(db, ruche_id=row.ruche_id, origin_instance_id=row.origin_instance_id, now=now)
        if view is not None:
            out.append(view)
    return out


def ranking_tier(view: PeerLoadView | None) -> int:
    """0 = capacité connue libre, 1 = neutre (inconnu ou périmé), 2 = occupé frais."""
    if view is None or view.stale:
        return 1
    if view.busy:
        return 2
    return 0
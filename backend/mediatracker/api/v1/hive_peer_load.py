"""Lecture des instantanés de charge pair (moniteur performance)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.config import Settings
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.membership import local_instance_identity, ruches_for_instance
from mediatracker.hive.transfer_load import build_local_load_snapshot, list_peer_loads_for_ruches
from mediatracker.hive.transfer_parallel import TransferParallelGate

router = APIRouter(tags=["hive"])


class HivePeerLoadItem(BaseModel):
    ruche_id_hex: str
    origin_instance_id: str
    out_active: int
    parallel_limit: int
    send_cap_bps: int
    send_avail_bps: float | None
    busy: bool
    snapshot_at: datetime
    received_at: datetime
    stale: bool


class HivePeerLoadSelf(BaseModel):
    instance_id: str
    out_active: int
    parallel_limit: int
    send_cap_bps: int
    send_avail_bps: float | None
    busy: bool
    snapshot_at: datetime


class HivePeerLoadList(BaseModel):
    self_load: HivePeerLoadSelf | None = Field(default=None, serialization_alias="self")
    items: list[HivePeerLoadItem]


def _require_hive(db: Session) -> None:
    if not hive_enabled_in_db(db):
        raise Problem(404, "Introuvable", "Hive est désactivé.")


@router.get("/peers/load", response_model=HivePeerLoadList)
def list_hive_peer_load(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> HivePeerLoadList:
    del _principal
    _require_hive(db)
    settings: Settings = request.app.state.settings
    try:
        _, instance_id, *_ = local_instance_identity(db, settings)
    except ValueError:
        return HivePeerLoadList(self_load=None, items=[])
    ruche_ids = [r.ruche_id for r in ruches_for_instance(db, instance_id)]
    now = datetime.now(timezone.utc)
    parallel_gate = getattr(request.app.state, "hive_transfer_parallel", None)
    send_bandwidth = getattr(request.app.state, "hive_send_bandwidth", None)
    gate = parallel_gate if isinstance(parallel_gate, TransferParallelGate) else None
    self_row: HivePeerLoadSelf | None = None
    snapshot = build_local_load_snapshot(
        db,
        now,
        parallel_gate=gate,
        send_bandwidth=send_bandwidth,
    )
    if snapshot is not None:
        at_raw = snapshot.get("at")
        snapshot_at = now
        if isinstance(at_raw, str):
            text = at_raw.strip().replace("Z", "+00:00")
            try:
                snapshot_at = datetime.fromisoformat(text)
                if snapshot_at.tzinfo is None:
                    snapshot_at = snapshot_at.replace(tzinfo=timezone.utc)
            except ValueError:
                snapshot_at = now
        self_row = HivePeerLoadSelf(
            instance_id=instance_id,
            out_active=int(snapshot["out_active"]),
            parallel_limit=int(snapshot["parallel_limit"]),
            send_cap_bps=int(snapshot["send_cap_bps"]),
            send_avail_bps=snapshot.get("send_avail_bps"),
            busy=bool(snapshot["busy"]),
            snapshot_at=snapshot_at,
        )
    views = list_peer_loads_for_ruches(db, ruche_ids, now)
    items = [
        HivePeerLoadItem(
            ruche_id_hex=v.ruche_id.hex(),
            origin_instance_id=v.origin_instance_id,
            out_active=v.out_active,
            parallel_limit=v.parallel_limit,
            send_cap_bps=v.send_cap_bps,
            send_avail_bps=v.send_avail_bps,
            busy=v.busy,
            snapshot_at=v.snapshot_at,
            received_at=v.received_at,
            stale=v.stale,
        )
        for v in views
    ]
    return HivePeerLoadList(self_load=self_row, items=items)
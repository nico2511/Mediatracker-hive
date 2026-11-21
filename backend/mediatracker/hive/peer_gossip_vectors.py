"""Derniers vecteurs catalogue / signaux annoncés par un pair (hint et pull)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

HIVE_PEER_GOSSIP_VECTORS_KEY = "hive_peer_gossip_vectors"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def sanitize_version_vector(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, int):
            out[key] = value
    return out


def _load_all(db: Session) -> dict[str, Any]:
    row = db.get(Setting, HIVE_PEER_GOSSIP_VECTORS_KEY)
    if row is None or not (row.value or "").strip():
        return {}
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_all(db: Session, state: dict[str, Any]) -> None:
    text = json.dumps(state, separators=(",", ":"), sort_keys=True)
    row = db.get(Setting, HIVE_PEER_GOSSIP_VECTORS_KEY)
    if row is None:
        db.add(Setting(key=HIVE_PEER_GOSSIP_VECTORS_KEY, value=text))
    else:
        row.value = text
    db.flush()


def load_peer_gossip_vectors(
    db: Session, *, ruche_id: bytes, peer_instance_id: str
) -> tuple[dict[str, int], dict[str, int]]:
    state = _load_all(db)
    ruche = state.get(ruche_id.hex())
    if not isinstance(ruche, dict):
        return {}, {}
    row = ruche.get(peer_instance_id)
    if not isinstance(row, dict):
        return {}, {}
    return sanitize_version_vector(row.get("vector")), sanitize_version_vector(row.get("signal_vector"))


def record_peer_gossip_vectors(
    db: Session,
    *,
    ruche_id: bytes,
    peer_instance_id: str,
    vector: object,
    signal_vector: object,
    now: datetime | None = None,
) -> tuple[dict[str, int], dict[str, int]]:
    catalog = sanitize_version_vector(vector)
    signals = sanitize_version_vector(signal_vector)
    moment = now or _utc_now()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    state = _load_all(db)
    ruche_key = ruche_id.hex()
    ruche = state.get(ruche_key)
    if not isinstance(ruche, dict):
        ruche = {}
    ruche[peer_instance_id] = {
        "vector": catalog,
        "signal_vector": signals,
        "at": moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    state[ruche_key] = ruche
    _save_all(db, state)
    return catalog, signals


def clear_peer_gossip_vectors_for_ruche(db: Session, *, ruche_id: bytes) -> None:
    state = _load_all(db)
    if ruche_id.hex() not in state:
        return
    state.pop(ruche_id.hex(), None)
    _save_all(db, state)

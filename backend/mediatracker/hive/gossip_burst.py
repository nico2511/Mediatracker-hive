"""Signal « j'ai du nouveau » regroupé sur 60 s (cadrage §3.3)."""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting
from mediatracker.hive.crypto_id import hive_public_id_from_public_key
from mediatracker.hive.gossip_exchange import send_gossip_hint_to_peer
from mediatracker.hive.gossip_worker import PeerExchangeTarget, public_peer_targets
from mediatracker.hive.identity import HIVE_PUBLIC_ADDRESS_KEY, _read_setting_text, hive_enabled_in_db, load_private_key
from mediatracker.hive.membership import ruches_for_instance

logger = logging.getLogger(__name__)

HIVE_BURST_STATE_KEY = "hive_gossip_burst_state"
_BURST_GROUP_SECONDS = 60.0


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_burst_state(db: Session) -> dict[str, dict[str, str]]:
    row = db.get(Setting, HIVE_BURST_STATE_KEY)
    if row is None or not (row.value or "").strip():
        return {}
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_burst_state(db: Session, state: dict[str, dict[str, str]]) -> None:
    text = json.dumps(state, separators=(",", ":"), sort_keys=True)
    row = db.get(Setting, HIVE_BURST_STATE_KEY)
    if row is None:
        db.add(Setting(key=HIVE_BURST_STATE_KEY, value=text))
    else:
        row.value = text
    db.flush()


def _public_address_configured(db: Session) -> bool:
    return bool(_read_setting_text(db, HIVE_PUBLIC_ADDRESS_KEY).strip())


def mark_local_catalog_change(db: Session, *, ruche_id: bytes, now: datetime) -> None:
    """Arme un signal gossip après changement catalogue local (une fenêtre 60 s par ruche)."""
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)
    if not hive_enabled_in_db(db):
        return
    if not _public_address_configured(db):
        return
    key = ruche_id.hex()
    state = _load_burst_state(db)
    if key in state:
        return
    moment = _utc(now)
    state[key] = {
        "send_after": (moment + timedelta(seconds=_BURST_GROUP_SECONDS)).isoformat().replace("+00:00", "Z"),
    }
    _save_burst_state(db, state)


def burst_due_ruches(db: Session, now: datetime) -> list[bytes]:
    if not hive_enabled_in_db(db):
        return []
    moment = _utc(now)
    state = _load_burst_state(db)
    due: list[bytes] = []
    for rid_hex, meta in state.items():
        if not isinstance(meta, dict):
            continue
        raw = meta.get("send_after")
        if not isinstance(raw, str):
            continue
        try:
            send_after = _utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
        if moment >= send_after:
            try:
                due.append(bytes.fromhex(rid_hex))
            except ValueError:
                continue
    return due


def clear_burst_arm(db: Session, *, ruche_id: bytes) -> None:
    state = _load_burst_state(db)
    state.pop(ruche_id.hex(), None)
    _save_burst_state(db, state)


def hive_gossip_burst_tick(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
    on_hint_sent: Callable[[PeerExchangeTarget], None] | None = None,
) -> int:
    """Envoie les signaux dus (0 si Hive éteint, tirage, ou rien à envoyer)."""
    moment = now or datetime.now(timezone.utc)
    if not hive_enabled_in_db(db) or not _public_address_configured(db):
        return 0
    pk = load_private_key(settings)
    if pk is None:
        return 0
    instance_id = hive_public_id_from_public_key(pk.public_key())
    due = burst_due_ruches(db, moment)
    if not due:
        return 0
    owned = {r.ruche_id for r in ruches_for_instance(db, instance_id)}
    sent = 0
    for rid in due:
        if rid not in owned:
            clear_burst_arm(db, ruche_id=rid)
            continue
        peers = public_peer_targets(
            db, ruche_id=rid, self_instance_id=instance_id, count=2, rng=rng
        )
        for member_id, base_url in peers:
            target = PeerExchangeTarget(ruche_id=rid, peer_base_url=base_url, peer_instance_id=member_id)
            try:
                send_gossip_hint_to_peer(
                    db,
                    settings,
                    ruche_id=rid,
                    peer_base_url=base_url,
                    now=moment,
                    peer_instance_id=member_id,
                )
                sent += 1
                if on_hint_sent is not None:
                    on_hint_sent(target)
            except Exception:
                logger.exception("Signal gossip Hive échoué (ruche %s).", rid.hex())
        clear_burst_arm(db, ruche_id=rid)
    return sent

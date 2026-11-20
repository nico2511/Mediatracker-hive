"""Époque de catalogue signée (liste membres + anti-réinjection gossip)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveEntry, HiveRuche, HiveSignal, HiveSignalVersions, HiveVersions


def ruche_confirm_name(ruche_id: bytes) -> str:
    return f"Ruche {ruche_id.hex()[:8]}"


def payload_catalog_epoch(payload: dict) -> int:
    raw = payload.get("catalog_epoch")
    if not isinstance(raw, int) or raw < 0:
        return 0
    return raw


def local_catalog_epoch(db: Session, ruche_id: bytes) -> int:
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        return 0
    return int(ruche.catalog_epoch or 0)


def catalog_reset_at_iso(ruche: HiveRuche | None) -> str | None:
    if ruche is None or ruche.catalog_reset_at is None:
        return None
    return ruche.catalog_reset_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def member_list_epoch_fields(db: Session, ruche_id: bytes) -> tuple[int, str | None]:
    ruche = db.get(HiveRuche, ruche_id)
    epoch = local_catalog_epoch(db, ruche_id)
    return epoch, catalog_reset_at_iso(ruche)


def sync_ruche_catalog_fields_from_payload(db: Session, *, ruche_id: bytes, payload: dict) -> None:
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        return
    if "catalog_epoch" in payload:
        ruche.catalog_epoch = payload_catalog_epoch(payload)
    raw = payload.get("catalog_reset_at")
    if isinstance(raw, str) and raw.strip():
        ruche.catalog_reset_at = datetime.fromisoformat(raw.replace("Z", "+00:00"))


def purge_ruche_catalog_and_signals(
    db: Session, *, ruche_id: bytes, now: datetime, settings: Settings | None = None
) -> None:
    db.execute(delete(HiveEntry).where(HiveEntry.ruche_id == ruche_id))
    db.execute(delete(HiveSignal).where(HiveSignal.ruche_id == ruche_id))
    db.execute(delete(HiveVersions).where(HiveVersions.ruche_id == ruche_id))
    db.execute(delete(HiveSignalVersions).where(HiveSignalVersions.ruche_id == ruche_id))
    from mediatracker.hive.hive_catalogue import refresh_hive_catalogue_store

    refresh_hive_catalogue_store(db, now=now, settings=settings)


def apply_catalog_epoch_increase_if_needed(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    payload: dict,
    now: datetime,
) -> bool:
    """Purge locale + republication si l'époque signée dépasse l'époque locale."""
    incoming = payload_catalog_epoch(payload)
    local = local_catalog_epoch(db, ruche_id)
    if incoming <= local:
        return False
    purge_ruche_catalog_and_signals(db, ruche_id=ruche_id, now=now, settings=settings)
    sync_ruche_catalog_fields_from_payload(db, ruche_id=ruche_id, payload=payload)
    from mediatracker.hive.catalog import sync_hive_catalog
    from mediatracker.hive.gossip_burst import mark_local_catalog_change

    sync_hive_catalog(db, settings, now=now)
    mark_local_catalog_change(db, ruche_id=ruche_id, now=now)
    return True

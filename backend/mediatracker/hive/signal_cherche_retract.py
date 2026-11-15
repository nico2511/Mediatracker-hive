"""Retrait volontaire d’une demande cherche : bloque republication automatique."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.db.models import HiveChercheRetract


def set_cherche_retract(db: Session, media_item_id: int, *, now: datetime) -> None:
    row = db.get(HiveChercheRetract, media_item_id)
    if row is None:
        db.add(HiveChercheRetract(media_item_id=media_item_id, retracted_at=now))
    else:
        row.retracted_at = now


def clear_cherche_retract(db: Session, media_item_id: int) -> None:
    row = db.get(HiveChercheRetract, media_item_id)
    if row is not None:
        db.delete(row)


def cherche_retract_active(db: Session, media_item_id: int) -> bool:
    return db.get(HiveChercheRetract, media_item_id) is not None
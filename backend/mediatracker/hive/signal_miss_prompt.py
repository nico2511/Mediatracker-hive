"""Invite d’annonce cherche (mode ask, après échec recherche habituelle)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.db.models import HiveChercheMissPrompt


def set_cherche_miss_prompt(db: Session, media_item_id: int, *, now: datetime) -> None:
    row = db.get(HiveChercheMissPrompt, media_item_id)
    if row is None:
        db.add(HiveChercheMissPrompt(media_item_id=media_item_id, prompted_at=now))
    else:
        row.prompted_at = now


def clear_cherche_miss_prompt(db: Session, media_item_id: int) -> None:
    row = db.get(HiveChercheMissPrompt, media_item_id)
    if row is not None:
        db.delete(row)


def cherche_miss_prompt_active(db: Session, media_item_id: int) -> bool:
    return db.get(HiveChercheMissPrompt, media_item_id) is not None
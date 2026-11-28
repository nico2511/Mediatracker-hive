"""Rang de profil (champ non sensible pour task_run)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.profile_attempt_orchestration import load_profile_chain
from mediatracker.core.profile_replacement import served_poll_profile_id
from mediatracker.db.models import Media, MediaItem


def rank_cause_for_profile_id(db: Session, media: Media, profile_id: int | None, *, settings: Settings) -> str | None:
    if profile_id is None:
        return None
    try:
        chain = load_profile_chain(db, media, settings)
    except Exception:
        return "rang=?"
    for index, profile in enumerate(chain.profiles, start=1):
        if int(profile.quality_profile_id) == int(profile_id):
            return f"rang={index}"
    return "rang=?"


def rank_cause_for_item(db: Session, media: Media, item: MediaItem, *, settings: Settings) -> str | None:
    profile_id = served_poll_profile_id(db, item)
    return rank_cause_for_profile_id(db, media, profile_id, settings=settings)

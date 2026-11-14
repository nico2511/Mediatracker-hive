"""Garde-fou anti-doublon à la création d'un ``Download``."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Download

# Cohérent avec api/v1/downloads.py et api/v1/links.py (états terminaux).
DOWNLOAD_TERMINAL_STATES = frozenset({"done", "failed", "cancelled"})

# États actifs connus du pipeline (équivalent à ``not_in(TERMINAL)`` pour ces valeurs).
DOWNLOAD_IN_PROGRESS_STATES = frozenset(
    {"pending", "resolving", "queued", "downloading", "post_processing"}
)

# Compatibilité SQL ``.in_(…)`` (force_download, source_poll, etc.).
DOWNLOAD_IN_PROGRESS = tuple(sorted(DOWNLOAD_IN_PROGRESS_STATES))


def is_download_in_progress_state(state: str) -> bool:
    return state not in DOWNLOAD_TERMINAL_STATES


def active_download_for_item(db: Session, item_id: int) -> Download | None:
    """Retourne un job non terminal pour cet épisode, ou ``None``."""
    return db.scalar(
        select(Download)
        .where(
            Download.media_item_id == item_id,
            Download.state.not_in(tuple(DOWNLOAD_TERMINAL_STATES)),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )

"""Gestion des exclusions de partage Hive (UI T7)."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveShareExclusion

# Exclusion « toutes les ruches » (16 octets nuls, jamais un id aléatoire).
RUCHE_EXCLUDE_ALL = bytes(16)


def list_excluded_ruche_ids(db: Session, media_item_id: int) -> tuple[bool, list[bytes]]:
    """Retourne (exclu partout, liste de ruches exclues)."""
    rows = list(
        db.scalars(select(HiveShareExclusion).where(HiveShareExclusion.media_item_id == media_item_id)).all()
    )
    if any(row.ruche_id == RUCHE_EXCLUDE_ALL for row in rows):
        return True, []
    return False, [row.ruche_id for row in rows if row.ruche_id != RUCHE_EXCLUDE_ALL]


def replace_share_exclusions(
    db: Session,
    media_item_id: int,
    *,
    exclude_all: bool,
    ruche_ids: list[bytes],
) -> None:
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)
    db.execute(delete(HiveShareExclusion).where(HiveShareExclusion.media_item_id == media_item_id))
    if exclude_all:
        db.add(HiveShareExclusion(media_item_id=media_item_id, ruche_id=RUCHE_EXCLUDE_ALL))
    else:
        for rid in ruche_ids:
            if len(rid) != 16:
                continue
            db.add(HiveShareExclusion(media_item_id=media_item_id, ruche_id=rid))
    db.flush()

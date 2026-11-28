"""Politique de partage catalogue Hive : manuel par défaut, auto-partage optionnel."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.db.models import HiveFileFingerprint, HiveShareIntent, Setting
from mediatracker.hive.share_exclusions import list_excluded_ruche_ids
from mediatracker.hive.transfer_settings import _read_bool

HIVE_CATALOG_AUTO_SHARE_KEY = "hive_catalog_auto_share"


def hive_catalog_auto_share_enabled(db: Session) -> bool:
    return _read_bool(db, HIVE_CATALOG_AUTO_SHARE_KEY, default=False)


def has_share_intent(db: Session, media_item_id: int) -> bool:
    return db.get(HiveShareIntent, media_item_id) is not None


def grant_share_intent(db: Session, media_item_id: int) -> None:
    if db.get(HiveShareIntent, media_item_id) is None:
        db.add(HiveShareIntent(media_item_id=media_item_id))
        from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

        mark_hive_catalog_sync_needed(db)
    db.flush()


def revoke_share_intent(db: Session, media_item_id: int) -> None:
    removed = db.execute(delete(HiveShareIntent).where(HiveShareIntent.media_item_id == media_item_id))
    if int(removed.rowcount or 0) > 0:
        from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

        mark_hive_catalog_sync_needed(db)
    db.flush()


def item_wants_hive_share(db: Session, media_item_id: int) -> bool:
    """Faut-il empreinter / publier cet épisode (hors cas API ou repartage explicite)."""
    exclude_all, _ = list_excluded_ruche_ids(db, media_item_id)
    if exclude_all:
        return False
    if hive_catalog_auto_share_enabled(db):
        return True
    return has_share_intent(db, media_item_id)


def effective_exclude_all_for_ui(db: Session, media_item_id: int) -> bool:
    """Statut UI : sans intention explicite en mode manuel, considéré comme non partagé."""
    exclude_all, _ = list_excluded_ruche_ids(db, media_item_id)
    if exclude_all:
        return True
    if hive_catalog_auto_share_enabled(db):
        return False
    return not has_share_intent(db, media_item_id)


def abandon_orphan_fingerprint_work(db: Session) -> int:
    """Abandonne les empreintes en cours pour les épisodes sans intention de partage."""
    if hive_catalog_auto_share_enabled(db):
        return 0
    removed = 0
    rows = list(
        db.scalars(select(HiveFileFingerprint).where(HiveFileFingerprint.sha256_hex.is_(None))).all()
    )
    for row in rows:
        media_item_id = int(row.media_item_id)
        if not item_wants_hive_share(db, media_item_id):
            db.delete(row)
            removed += 1
    if removed:
        db.flush()
    return removed


def ensure_hive_catalog_auto_share_default(db: Session) -> bool:
    """Pose hive_catalog_auto_share=false si absent (idempotent). Retourne True si inséré."""
    row = db.get(Setting, HIVE_CATALOG_AUTO_SHARE_KEY)
    if row is not None and (row.value or "").strip():
        return False
    db.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="false"))
    db.flush()
    return True

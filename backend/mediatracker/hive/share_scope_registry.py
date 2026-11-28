"""Persistance des périmètres partagés (fiche / saison) et héritage au sync catalogue."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import HiveShareScope, HiveShareScopeExclusion
from mediatracker.hive.share_exclusions import RUCHE_EXCLUDE_ALL, replace_share_exclusions
from mediatracker.hive.share_policy import grant_share_intent, has_share_intent, hive_catalog_auto_share_enabled
from mediatracker.hive.share_scope import scope_media_items


def _scope_row(db: Session, media_id: int, *, season: int | None) -> HiveShareScope | None:
    q = select(HiveShareScope).where(HiveShareScope.media_id == media_id)
    if season is None:
        q = q.where(HiveShareScope.season.is_(None))
    else:
        q = q.where(HiveShareScope.season == season)
    return db.scalars(q).first()


def list_scope_excluded_ruche_ids(db: Session, scope_id: int) -> tuple[bool, list[bytes]]:
    rows = list(
        db.scalars(select(HiveShareScopeExclusion).where(HiveShareScopeExclusion.scope_id == scope_id)).all()
    )
    if any(row.ruche_id == RUCHE_EXCLUDE_ALL for row in rows):
        return True, []
    return False, [row.ruche_id for row in rows if row.ruche_id != RUCHE_EXCLUDE_ALL]


def replace_scope_share_exclusions(
    db: Session,
    scope_id: int,
    *,
    exclude_all: bool,
    ruche_ids: list[bytes],
) -> None:
    db.execute(delete(HiveShareScopeExclusion).where(HiveShareScopeExclusion.scope_id == scope_id))
    if exclude_all:
        db.add(HiveShareScopeExclusion(scope_id=scope_id, ruche_id=RUCHE_EXCLUDE_ALL))
    else:
        for rid in ruche_ids:
            if len(rid) != 16:
                continue
            db.add(HiveShareScopeExclusion(scope_id=scope_id, ruche_id=rid))
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)
    db.flush()


def persist_share_scope(
    db: Session,
    media_id: int,
    *,
    season: int | None,
    ruche_ids: list[bytes],
) -> HiveShareScope:
    row = _scope_row(db, media_id, season=season)
    if row is None:
        row = HiveShareScope(media_id=media_id, season=season)
        db.add(row)
        db.flush()
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)
    replace_scope_share_exclusions(db, int(row.id), exclude_all=False, ruche_ids=ruche_ids)
    return row


def clear_share_scope(db: Session, media_id: int, *, season: int | None) -> None:
    """Retire le périmètre en base (fiche entière => toutes les saisons de la fiche).

    Retrait d'une saison seule sous partage fiche : conserve une ligne saison marquée ``exclude_all``
    pour bloquer l'héritage depuis le périmètre fiche.
    """
    if season is None:
        db.execute(delete(HiveShareScope).where(HiveShareScope.media_id == media_id))
    else:
        whole_fiche = _scope_row(db, media_id, season=None)
        if whole_fiche is not None:
            row = _scope_row(db, media_id, season=season)
            if row is None:
                row = HiveShareScope(media_id=media_id, season=season)
                db.add(row)
                db.flush()
            replace_scope_share_exclusions(db, int(row.id), exclude_all=True, ruche_ids=[])
        else:
            row = _scope_row(db, media_id, season=season)
            if row is not None:
                db.delete(row)
    from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

    mark_hive_catalog_sync_needed(db)
    db.flush()


def excluded_seasons_under_fiche_scope(db: Session, media_id: int) -> set[int]:
    """Saisons explicitement retirées alors que la fiche entière reste partagée."""
    blocked: set[int] = set()
    rows = list(
        db.scalars(
            select(HiveShareScope).where(
                HiveShareScope.media_id == media_id,
                HiveShareScope.season.is_not(None),
            )
        ).all()
    )
    for scope in rows:
        scope_exclude_all, _ = list_scope_excluded_ruche_ids(db, int(scope.id))
        if scope_exclude_all and scope.season is not None:
            blocked.add(int(scope.season))
    return blocked


def inherit_share_intent_from_scopes(db: Session, settings: Settings, *, moment) -> None:
    """Accorde intention + exclusions aux fichiers locaux du périmètre sans intention explicite.

    Un épisode retiré individuellement conserve ``RUCHE_EXCLUDE_ALL`` sur l'item : il n'est pas
    ré-accordé tant que l'exclusion item n'est pas levée (API épisode).
    """
    if hive_catalog_auto_share_enabled(db):
        return
    from mediatracker.hive.catalog import sync_hive_catalog_item
    from mediatracker.hive.share_exclusions import list_excluded_ruche_ids

    scopes = list(db.scalars(select(HiveShareScope).order_by(HiveShareScope.id.asc())).all())
    for scope in scopes:
        scope_id = int(scope.id)
        scope_exclude_all, scope_ruche_ids = list_scope_excluded_ruche_ids(db, scope_id)
        if scope_exclude_all:
            continue
        media_id = int(scope.media_id)
        blocked_seasons = excluded_seasons_under_fiche_scope(db, media_id) if scope.season is None else set()
        for item in scope_media_items(db, media_id, season=scope.season):
            if scope.season is None and item.season is not None and int(item.season) in blocked_seasons:
                continue
            assert item.id is not None
            item_id = int(item.id)
            item_exclude_all, _ = list_excluded_ruche_ids(db, item_id)
            if item_exclude_all or has_share_intent(db, item_id):
                continue
            replace_share_exclusions(db, item_id, exclude_all=False, ruche_ids=scope_ruche_ids)
            grant_share_intent(db, item_id)
            sync_hive_catalog_item(db, settings, item_id, now=moment)
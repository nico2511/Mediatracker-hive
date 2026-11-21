"""Données Accueil — dernières disponibilités catalogue Hive (non possédées)."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.tracking import tmdb_page_url
from mediatracker.db.models import HiveCatalogue, HiveEntry, Media
from mediatracker.hive.catalogue_card import aggregate_catalogue_works, catalogue_work_is_fully_owned
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.signal import item_file_present
from mediatracker.hive.signal_wanted import media_type_from_cle

_SHARE_POOL_FACTOR = 15
_SHARE_POOL_MIN = 120


def _share_ranked_work_keys(db: Session, *, pool: int) -> list[tuple[str, datetime]]:
    """Dernières annonces : fenêtre SQL récente, agrégat max(created_at) par work_key en mémoire."""
    rows = db.execute(
        select(HiveEntry.cle_json, HiveEntry.created_at)
        .where(
            HiveEntry.etat == "disponible",
            HiveEntry.tombstone_at.is_(None),
            HiveEntry.cle_json.is_not(None),
        )
        .order_by(HiveEntry.created_at.desc())
        .limit(pool)
    ).all()
    best: dict[str, datetime] = {}
    for cle_json, created_at in rows:
        try:
            cle = json.loads(cle_json or "{}")
        except json.JSONDecodeError:
            continue
        if not isinstance(cle, dict):
            continue
        wk = _stable_work_key(cle)
        prev = best.get(wk)
        if prev is None or created_at > prev:
            best[wk] = created_at
    return sorted(best.items(), key=lambda pair: pair[1], reverse=True)


def _works_for_catalogue_keys(
    db: Session,
    settings: Settings,
    work_keys: list[str],
) -> dict[str, dict]:
    if not work_keys:
        return {}
    from mediatracker.api.v1.hive_ui import (  # noqa: PLC2701
        _catalogue_version_fields,
        _display_meta,
        _local_media_item,
        _parse_json,
        _parse_sources_json,
        _parse_version_parts,
        _sanitize_sources,
    )

    rows = list(db.scalars(select(HiveCatalogue).where(HiveCatalogue.work_key.in_(work_keys))).all())
    works: dict[str, dict] = {}
    for row in rows:
        cle = _parse_json(row.cle_json)
        wk = row.work_key
        if wk not in works:
            meta = _display_meta(db, cle)
            item = _local_media_item(db, cle)
            works[wk] = {
                "work_key": wk,
                "type": media_type_from_cle(cle),
                "tmdb_id": cle.get("tmdb_id"),
                "season": cle.get("saison"),
                "episode": cle.get("episode"),
                "title": meta["title"],
                "year": meta.get("year"),
                "poster_url": meta.get("poster_url"),
                "overview": meta.get("overview"),
                "disponible": False,
                "versions": [],
                "in_library": item is not None and item_file_present(db, item),
                "media_id": item.media_id if item else None,
                "media_item_id": item.id if item else None,
            }
        src_list = _parse_sources_json(row.sources_json)
        quality, language = _parse_version_parts(row.version_key)
        extra = _catalogue_version_fields(db, src_list)
        works[wk]["versions"].append(
            {
                "version_key": row.version_key,
                "disponible": bool(row.disponible),
                "source_count": _sanitize_sources(src_list),
                "quality": quality,
                "language": language,
                "recommended": False,
                **extra,
            }
        )
        works[wk]["disponible"] = works[wk]["disponible"] or bool(row.disponible)
    merged = aggregate_catalogue_works(db, settings, list(works.values()))
    return {str(w["work_key"]): w for w in merged if w.get("work_key")}


def list_hive_dispos_for_home(db: Session, settings: Settings, *, limit: int = 20) -> list[dict]:
    """Œuvres Hive disponibles, non en bibliothèque, triées par dernière annonce."""
    if not hive_enabled_in_db(db):
        return []
    from mediatracker.api.v1.hive_ui import _enrich_work  # noqa: PLC2701

    pool = max(limit * _SHARE_POOL_FACTOR, _SHARE_POOL_MIN)
    ranked = _share_ranked_work_keys(db, pool=pool)
    if not ranked:
        return []

    keys = [wk for wk, _ in ranked]
    works_by_key = _works_for_catalogue_keys(db, settings, keys)

    picked: list[dict] = []
    for wk, _ in ranked:
        work = works_by_key.get(wk)
        if work is None:
            continue
        if not work.get("disponible") or catalogue_work_is_fully_owned(work):
            continue
        if work.get("type") not in ("film", "series"):
            continue
        picked.append(work)
        if len(picked) >= limit:
            break

    out: list[dict] = []
    for work in picked:
        enriched = _enrich_work(db, settings, work)
        media_type = "film" if enriched.get("type") == "film" else "series"
        tmdb_id = enriched.get("tmdb_id")
        media_id = enriched.get("media_id")
        if media_id is None and tmdb_id is not None:
            media = db.scalar(
                select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1)
            )
            if media is not None:
                media_id = media.id
        versions = enriched.get("versions") or []
        version_count = len(versions) if isinstance(versions, list) else 0
        subtitle = (
            f"{version_count} version{'s' if version_count > 1 else ''}"
            if version_count
            else None
        )
        out.append(
            {
                "title": str(enriched.get("title") or "Œuvre fictive"),
                "subtitle": subtitle,
                "poster_url": enriched.get("poster_url"),
                "media_type": media_type,
                "media_id": media_id,
                "year": enriched.get("year"),
                "overview": enriched.get("overview"),
                "tmdb_id": tmdb_id,
                "tmdb_url": tmdb_page_url(media_type, int(tmdb_id)) if tmdb_id is not None else None,
                "catalog_id": None,
                "followed": bool(enriched.get("followed")),
                "source_url": None,
                "in_library": False,
                "ignored": False,
                "hive_work_key": str(enriched.get("work_key") or ""),
            }
        )
    return out

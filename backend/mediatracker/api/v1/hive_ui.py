"""Hive T7 : API lecture interface (catalogue fusionné, partage)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.config import Settings
from mediatracker.core.follow import FollowError, FollowInput, follow
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveCatalogue,
    HiveEntry,
    HiveFileFingerprint,
    Media,
    MediaItem,
)
from mediatracker.hive.catalog import (
    _gossip_poster_url,
    _public_address_configured,
    catalog_entry_local_for_item,
    item_is_shareable_base,
    sync_hive_catalog_item,
)
from mediatracker.hive.catalogue_card import (
    aggregate_catalogue_works,
    compute_card_fields,
    pick_episode_work,
)
from mediatracker.hive.download_aria2 import hive_download_active_for_item
from mediatracker.hive.hive_catalogue import load_hive_catalogue_rows, refresh_hive_catalogue_store
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.share_exclusions import list_excluded_ruche_ids, replace_share_exclusions
from mediatracker.hive.share_policy import effective_exclude_all_for_ui, grant_share_intent, revoke_share_intent
from mediatracker.hive.source_catalog import (
    _expected_size_for_sha,
    _parse_version_parts,
    rank_catalog_version_for_profile_attempt,
)
from mediatracker.hive.source_download import try_enqueue_hive_for_item

router = APIRouter(tags=["hive"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_hive(db: Session) -> None:
    if not hive_enabled_in_db(db):
        raise Problem(404, "Introuvable", "Hive est désactivé.")


def _parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_sources_json(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    return [s for s in data if isinstance(s, dict)]


def _sanitize_sources(sources: list) -> int:
    return len([s for s in sources if isinstance(s, dict)])


def _fiche_matches_cle(fiche: dict[str, Any], cle: dict[str, Any]) -> bool:
    tmdb_id = cle.get("tmdb_id")
    if fiche.get("tmdb_id") != tmdb_id:
        return False
    if cle.get("type") == "series":
        return fiche.get("season") == cle.get("saison") and fiche.get("episode") == cle.get("episode")
    return True


def _stable_tmdb_poster_for_cle(db: Session, cle: dict[str, Any]) -> str | None:
    """Règle A28 : même identifiant TMDB (+ créneau série) → même URL TMDB (min lexicographique)."""
    posters: list[str] = []
    item = _local_media_item(db, cle)
    if item is not None:
        media = db.get(Media, item.media_id)
        if media is not None:
            poster = _gossip_poster_url(media.poster_url)
            if poster:
                posters.append(poster)
    q = (
        select(HiveEntry.fiche_json)
        .where(HiveEntry.cle_json.is_not(None), HiveEntry.etat == "disponible")
        .order_by(HiveEntry.id.desc())
        .limit(500)
    )
    for fiche_json in db.scalars(q).all():
        fiche = _parse_json(fiche_json or "")
        if not _fiche_matches_cle(fiche, cle):
            continue
        poster = _gossip_poster_url(fiche.get("poster_url") if isinstance(fiche.get("poster_url"), str) else None)
        if poster:
            posters.append(poster)
    if not posters:
        return None
    return min(posters)


def _display_meta(db: Session, cle: dict[str, Any]) -> dict[str, Any]:
    tmdb_id = cle.get("tmdb_id")
    stable_poster = _stable_tmdb_poster_for_cle(db, cle)
    q = (
        select(HiveEntry.fiche_json)
        .where(HiveEntry.cle_json.is_not(None), HiveEntry.etat == "disponible")
        .order_by(HiveEntry.id.desc())
        .limit(200)
    )
    for fiche_json in db.scalars(q).all():
        fiche = _parse_json(fiche_json or "")
        if not _fiche_matches_cle(fiche, cle):
            continue
        title = fiche.get("title")
        if isinstance(title, str) and title.strip():
            out = {
                "title": title.strip(),
                "year": fiche.get("year"),
                "poster_url": stable_poster,
            }
            overview = fiche.get("overview")
            if isinstance(overview, str) and overview.strip():
                out["overview"] = overview.strip()
            return out
    item = _local_media_item(db, cle)
    if item is not None:
        media = db.get(Media, item.media_id)
        if media is not None:
            out = {
                "title": media.title,
                "year": media.year,
                "poster_url": stable_poster,
            }
            overview = (media.overview or "").strip()
            if overview:
                out["overview"] = overview
            return out
    fallback = f"Œuvre fictive {tmdb_id}" if tmdb_id is not None else "Œuvre fictive"
    return {"title": fallback, "year": cle.get("year"), "poster_url": stable_poster}


def _catalogue_version_fields(db: Session, sources: list[dict[str, Any]]) -> dict[str, Any]:
    ruche_hex: set[str] = set()
    size_bytes: int | None = None
    for src in sources:
        if not isinstance(src, dict):
            continue
        rid = src.get("ruche_id_hex")
        if isinstance(rid, str) and rid.strip():
            ruche_hex.add(rid.strip().casefold())
        sha = src.get("sha256")
        if isinstance(sha, str) and sha.strip():
            sz = _expected_size_for_sha(db, sha.strip())
            if sz > 0:
                size_bytes = max(size_bytes or 0, sz)
    return {
        "ruche_count": len(ruche_hex) if ruche_hex else None,
        "size_bytes": size_bytes,
    }


def _mark_recommended_versions(db: Session, settings: Settings, work: dict[str, Any]) -> None:
    versions = work.get("versions")
    if not isinstance(versions, list) or not versions:
        return
    for ver in versions:
        if isinstance(ver, dict):
            ver["recommended"] = False
    tmdb_id = work.get("tmdb_id")
    media_type = work.get("type")
    if tmdb_id is None or media_type is None:
        return
    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is None:
        return
    from mediatracker.core.profile_attempt_orchestration import load_profile_chain
    from mediatracker.core.profile_fallback import ProfileResolveError

    try:
        profile = load_profile_chain(db, media, settings).principal
    except ProfileResolveError:
        return
    best_key: str | None = None
    best_rank: int | None = None
    for ver in versions:
        if not isinstance(ver, dict):
            continue
        vk = str(ver.get("version_key") or "")
        if not vk or not ver.get("disponible"):
            continue
        rank = rank_catalog_version_for_profile_attempt(
            vk,
            profile=profile,
            size=ver.get("size_bytes"),
        )
        if rank is None:
            continue
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_key = vk
    if best_key is None:
        return
    for ver in versions:
        if isinstance(ver, dict) and ver.get("version_key") == best_key:
            ver["recommended"] = True


def _local_media_item(db: Session, cle: dict[str, Any]) -> MediaItem | None:
    tmdb_id = cle.get("tmdb_id")
    if tmdb_id is None:
        return None
    media_type = cle.get("type")
    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is None:
        return None
    if media_type == "film":
        return db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
    season, episode = cle.get("saison"), cle.get("episode")
    return db.scalar(
        select(MediaItem).where(
            MediaItem.media_id == media.id,
            MediaItem.season == season,
            MediaItem.episode == episode,
        ).limit(1)
    )


def _works_index(db: Session) -> dict[str, dict[str, Any]]:
    works: dict[str, dict[str, Any]] = {}
    for row in load_hive_catalogue_rows(db):
        cle = _parse_json(row.cle_json)
        wk = row.work_key
        if wk not in works:
            meta = _display_meta(db, cle)
            item = _local_media_item(db, cle)
            works[wk] = {
                "work_key": wk,
                "type": cle.get("type"),
                "tmdb_id": cle.get("tmdb_id"),
                "season": cle.get("saison"),
                "episode": cle.get("episode"),
                "title": meta["title"],
                "year": meta.get("year"),
                "poster_url": meta.get("poster_url"),
                "overview": meta.get("overview"),
                "disponible": False,
                "versions": [],
                "in_library": item is not None and bool(item.downloaded),
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
    return works


def _episode_works_index(db: Session) -> dict[str, dict[str, Any]]:
    return _works_index(db)


def _aggregated_works_index(db: Session, settings: Settings) -> dict[str, dict[str, Any]]:
    raw = list(_works_index(db).values())
    merged = aggregate_catalogue_works(db, settings, raw)
    return {w["work_key"]: w for w in merged}


def _work_row(db: Session, settings: Settings, work_key: str) -> dict[str, Any] | None:
    episode_index = _episode_works_index(db)
    if work_key in episode_index:
        return episode_index[work_key]
    return _aggregated_works_index(db, settings).get(work_key)


def _enrich_work(db: Session, settings: Settings, work: dict[str, Any]) -> dict[str, Any]:
    out = dict(work)
    _mark_recommended_versions(db, settings, out)
    out.update(compute_card_fields(db, settings, work))
    return out


class HiveOverviewOut(BaseModel):
    enabled: bool


@router.get("/overview", response_model=HiveOverviewOut)
def get_hive_overview(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> HiveOverviewOut:
    del _principal
    return HiveOverviewOut(enabled=hive_enabled_in_db(db))


class CatalogueVersionOut(BaseModel):
    version_key: str
    disponible: bool
    source_count: int
    quality: str | None = None
    language: str | None = None
    size_bytes: int | None = None
    ruche_count: int | None = None
    recommended: bool = False


class CatalogueWorkOut(BaseModel):
    work_key: str
    type: str | None = None
    tmdb_id: int | None = None
    season: int | None = None
    episode: int | None = None
    title: str
    year: int | None = None
    poster_url: str | None = None
    overview: str | None = None
    disponible: bool
    in_library: bool
    media_id: int | None = None
    media_item_id: int | None = None
    versions: list[CatalogueVersionOut]
    card_state: str | None = None
    state_label: str | None = None
    state_label_short: str | None = None
    progress_pct: float | None = None
    error_line: str | None = None
    followed: bool | None = None
    shared_by_me: bool | None = None
    on_disk: bool | None = None
    tone: str | None = None
    episode_count: int | None = None
    episodes: list[dict[str, Any]] | None = None


class CataloguePageOut(BaseModel):
    items: list[CatalogueWorkOut]
    refreshed_at: str | None = None


@router.get("/catalogue", response_model=CataloguePageOut)
def get_hive_catalogue(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    q: str | None = None,
    media_type: str | None = Query(default=None, pattern="^(film|series)$"),
    version: str | None = None,
    hide_owned: bool = False,
    refresh: bool = False,
) -> CataloguePageOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    moment = _now()
    if refresh:
        refresh_hive_catalogue_store(db, now=moment, settings=settings)
        db.commit()
    works = aggregate_catalogue_works(db, settings, list(_works_index(db).values()))
    needle = (q or "").strip().casefold()
    if media_type:
        works = [w for w in works if w.get("type") == media_type]
    if hide_owned:
        works = [w for w in works if not w.get("in_library")]
    if version:
        vk = version.strip()
        works = [w for w in works if any(v["version_key"] == vk for v in w.get("versions") or [])]
    if needle:
        works = [w for w in works if needle in str(w.get("title", "")).casefold()]
    refreshed = db.scalar(select(HiveCatalogue.refreshed_at).order_by(HiveCatalogue.refreshed_at.desc()).limit(1))
    items = [_enrich_work(db, settings, w) for w in works]
    return CataloguePageOut(
        items=[CatalogueWorkOut.model_validate(w) for w in items],
        refreshed_at=refreshed.isoformat() if refreshed is not None else None,
    )


@router.get("/catalogue/{work_key}", response_model=CatalogueWorkOut)
def get_hive_catalogue_work(
    work_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> CatalogueWorkOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    work = _work_row(db, settings, work_key)
    if work is None:
        raise Problem(404, "Introuvable", "Œuvre absente du catalogue Hive.")
    payload = _enrich_work(db, settings, work)
    if isinstance(work.get("episodes"), list):
        payload["episodes"] = [_enrich_work(db, settings, ep) for ep in work["episodes"]]
    return CatalogueWorkOut.model_validate(payload)


class ShareStateOut(BaseModel):
    media_item_id: int
    exclude_all: bool
    excluded_ruche_ids: list[str]
    shareable: bool
    preparing: bool
    shared: bool
    transfer_active: bool
    transfer_progress_pct: float | None = None
    fingerprint_partial_size: int | None = None
    fingerprint_total_size: int | None = None
    fingerprint_error: str | None = None
    hive_keep: bool
    keep: bool = False
    pull_only: bool = False
    has_file: bool = False
    catalog_entry_local: bool = False


def _transfer_progress(db: Session, *, sha: str, item: MediaItem) -> tuple[bool, float | None]:
    if not sha or item.id is None:
        return False, None
    if not hive_download_active_for_item(db, item, sha):
        return False, None
    row = db.scalar(
        select(Download)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(
            Download.media_item_id == item.id,
            DownloadHiveFetch.sha256_hex == str(sha).lower(),
            Download.state.in_(("pending", "resolving", "queued", "downloading", "post_processing")),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )
    if row is None:
        return True, None
    expected = max(int(row.size or 0), 1)
    done = max(0, int(row.progress or 0))
    return True, min(100.0, round(100.0 * done / expected, 1))


def _share_state_for_item(db: Session, item: MediaItem, *, settings: Settings) -> ShareStateOut:
    assert item.id is not None
    exclude_all, excluded = list_excluded_ruche_ids(db, item.id)
    exclude_all_ui = effective_exclude_all_for_ui(db, item.id)
    shareable = item_is_shareable_base(db, item, hive_enabled=True)
    fp = db.get(HiveFileFingerprint, item.id)
    preparing = fp is not None and not (fp.sha256_hex or "").strip() and bool(item.file_path)
    sha = (fp.sha256_hex or "") if fp else ""
    transfer_active, transfer_pct = _transfer_progress(db, sha=sha, item=item)
    shared = shareable and not exclude_all_ui
    pull_only = hive_enabled_in_db(db) and not _public_address_configured(db)
    has_file = bool(item.file_path or item.downloaded)
    catalog_local = catalog_entry_local_for_item(db, settings, item)
    fp_partial = int(fp.partial_size or 0) if fp is not None else None
    fp_total = int(fp.size or 0) if fp is not None and int(fp.size or 0) > 0 else None
    if preparing and fp is not None and not (fp.sha256_hex or "").strip() and fp_total and fp_partial is not None:
        if fp_partial >= fp_total:
            fp_partial = max(fp_total - 1, 0)
    fp_error = (fp.hash_error or "").strip() if fp is not None else None
    return ShareStateOut(
        media_item_id=item.id,
        exclude_all=exclude_all_ui,
        excluded_ruche_ids=[rid.hex() for rid in excluded],
        shareable=shareable,
        preparing=preparing and not shareable,
        shared=shared,
        transfer_active=transfer_active,
        transfer_progress_pct=transfer_pct,
        fingerprint_partial_size=fp_partial if preparing else None,
        fingerprint_total_size=fp_total if preparing else None,
        fingerprint_error=fp_error or None,
        hive_keep=item.keep_source == "hive",
        keep=bool(item.keep),
        pull_only=pull_only,
        has_file=has_file,
        catalog_entry_local=catalog_local,
    )


class MediaShareSummaryOut(BaseModel):
    media_id: int
    shared: bool
    transfer_active: bool
    preparing: bool
    pull_only: bool


@router.get("/media/{media_id}/sharing-summary", response_model=MediaShareSummaryOut)
def media_sharing_summary(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> MediaShareSummaryOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media_id)).all())
    shared = False
    transfer_active = False
    preparing = False
    pull_only = hive_enabled_in_db(db) and not _public_address_configured(db)
    for item in items:
        row = _share_state_for_item(db, item, settings=settings)
        shared = shared or row.shared
        transfer_active = transfer_active or row.transfer_active
        preparing = preparing or row.preparing
    return MediaShareSummaryOut(
        media_id=media_id,
        shared=shared,
        transfer_active=transfer_active,
        preparing=preparing,
        pull_only=pull_only,
    )


@router.get("/items/sharing", response_model=list[ShareStateOut])
def batch_item_sharing(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    ids: str = "",
) -> list[ShareStateOut]:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    raw = [part.strip() for part in ids.split(",") if part.strip()]
    out: list[ShareStateOut] = []
    for part in raw:
        try:
            item_id = int(part)
        except ValueError:
            continue
        item = db.get(MediaItem, item_id)
        if item is None:
            continue
        out.append(_share_state_for_item(db, item, settings=settings))
    return out


@router.get("/items/{media_item_id}/sharing", response_model=ShareStateOut)
def get_item_sharing(
    media_item_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> ShareStateOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    item = db.get(MediaItem, media_item_id)
    if item is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    return _share_state_for_item(db, item, settings=settings)


class SharePatchIn(BaseModel):
    exclude_all: bool = False
    excluded_ruche_ids: list[str] = Field(default_factory=list)


@router.patch("/items/{media_item_id}/sharing", status_code=204)
def patch_item_sharing(
    media_item_id: int,
    body: SharePatchIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    if db.get(MediaItem, media_item_id) is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    ruche_ids: list[bytes] = []
    for raw in body.excluded_ruche_ids:
        text = raw.strip()
        if len(text) != 32:
            raise Problem(422, "Requête invalide", "Identifiant de ruche invalide.")
        try:
            ruche_ids.append(bytes.fromhex(text))
        except ValueError as exc:
            raise Problem(422, "Requête invalide", "Identifiant de ruche invalide.") from exc
    moment = _now()
    replace_share_exclusions(db, media_item_id, exclude_all=body.exclude_all, ruche_ids=ruche_ids)
    if body.exclude_all:
        revoke_share_intent(db, media_item_id)
    else:
        grant_share_intent(db, media_item_id)
    sync_hive_catalog_item(db, _settings(request), media_item_id, now=moment)
    refresh_hive_catalogue_store(db, now=moment, settings=_settings(request))
    from mediatracker.core.worker_wake import REASON_API_HIVE, wake_worker

    wake_worker(db, _settings(request), REASON_API_HIVE, commit=False)
    db.commit()


class SharingScopeOut(BaseModel):
    media_id: int
    season: int | None = None
    file_count: int
    shared_count: int
    any_shared: bool
    can_act: bool
    pull_only: bool
    message: str | None = None


class EmpreinteFileRowOut(BaseModel):
    media_item_id: int
    season: int | None = None
    episode: int | None = None
    slot_label: str
    file_state: str
    file_detail: str
    chunk_state: str
    chunk_detail: str
    phase: str
    chunk_progress_pct: float | None = None
    metadata_inconsistent: bool = False


class EmpreintesPageOut(BaseModel):
    media_id: int
    season: int | None = None
    ready_count: int
    pending_count: int
    total: int
    rows: list[EmpreinteFileRowOut]


class EmpreintesRecalcOut(BaseModel):
    scheduled_chunks: int
    in_progress_chunks: int
    file_sha_queued: int
    skipped_no_intent: int
    skipped_no_fingerprint: int
    claims_recovered: int
    wake_sent: bool


def _sharing_scope_out(db: Session, media_id: int, *, settings: Settings, season: int | None) -> SharingScopeOut:
    from mediatracker.hive.share_scope import ensure_media_exists, sharing_scope_summary

    ensure_media_exists(db, media_id)
    summary = sharing_scope_summary(db, media_id, settings=settings, season=season)
    message: str | None = None
    can_act = summary.file_count > 0 and not summary.pull_only
    if summary.pull_only and summary.file_count > 0:
        message = "Mode tirage : cette instance ne publie pas vers Hive."
    elif summary.file_count == 0:
        message = "Aucun fichier local dans ce périmètre."
    return SharingScopeOut(
        media_id=media_id,
        season=season,
        file_count=summary.file_count,
        shared_count=summary.shared_count,
        any_shared=summary.any_shared,
        can_act=can_act,
        pull_only=summary.pull_only,
        message=message,
    )


def _empreintes_page(db: Session, media_id: int, *, season: int | None) -> EmpreintesPageOut:
    from mediatracker.hive.fingerprint_details import empreintes_summary, fingerprint_rows_for_scope
    from mediatracker.hive.share_scope import ensure_media_exists

    ensure_media_exists(db, media_id)
    raw = fingerprint_rows_for_scope(db, media_id, season=season)
    stats = empreintes_summary(raw)
    rows = [
        EmpreinteFileRowOut(
            media_item_id=r.media_item_id,
            season=r.season,
            episode=r.episode,
            slot_label=r.slot_label,
            file_state=r.file_state,
            file_detail=r.file_detail,
            chunk_state=r.chunk_state,
            chunk_detail=r.chunk_detail,
            phase=r.phase,
            chunk_progress_pct=r.chunk_progress_pct,
            metadata_inconsistent=r.metadata_inconsistent,
        )
        for r in raw
    ]
    return EmpreintesPageOut(
        media_id=media_id,
        season=season,
        ready_count=stats["ready_count"],
        pending_count=stats["pending_count"],
        total=stats["total"],
        rows=rows,
    )


def _parse_ruche_ids(raw_ids: list[str]) -> list[bytes]:
    ruche_ids: list[bytes] = []
    for raw in raw_ids:
        text = raw.strip()
        if len(text) != 32:
            raise Problem(422, "Requête invalide", "Identifiant de ruche invalide.")
        try:
            ruche_ids.append(bytes.fromhex(text))
        except ValueError as exc:
            raise Problem(422, "Requête invalide", "Identifiant de ruche invalide.") from exc
    return ruche_ids


@router.get("/media/{media_id}/sharing-scope", response_model=SharingScopeOut)
def get_media_sharing_scope(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SharingScopeOut:
    del _principal
    _require_hive(db)
    return _sharing_scope_out(db, media_id, settings=_settings(request), season=None)


@router.patch("/media/{media_id}/sharing", status_code=204)
def patch_media_sharing_scope(
    media_id: int,
    body: SharePatchIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    from mediatracker.hive.share_scope import apply_sharing_to_scope, ensure_media_exists

    ensure_media_exists(db, media_id)
    scope = _sharing_scope_out(db, media_id, settings=_settings(request), season=None)
    if scope.file_count == 0:
        raise Problem(422, "Requête invalide", scope.message or "Aucun fichier local.")
    if scope.pull_only:
        raise Problem(422, "Requête invalide", scope.message or "Publication impossible.")
    ruche_ids = _parse_ruche_ids(body.excluded_ruche_ids)
    moment = _now()
    touched = apply_sharing_to_scope(
        db,
        _settings(request),
        media_id,
        season=None,
        exclude_all=body.exclude_all,
        ruche_ids=ruche_ids,
        moment=moment,
    )
    if not touched:
        raise Problem(422, "Requête invalide", "Aucun fichier local dans ce périmètre.")
    db.commit()


@router.get("/media/{media_id}/seasons/{season}/sharing-scope", response_model=SharingScopeOut)
def get_season_sharing_scope(
    media_id: int,
    season: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SharingScopeOut:
    del _principal
    _require_hive(db)
    return _sharing_scope_out(db, media_id, settings=_settings(request), season=season)


@router.patch("/media/{media_id}/seasons/{season}/sharing", status_code=204)
def patch_season_sharing_scope(
    media_id: int,
    season: int,
    body: SharePatchIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    from mediatracker.hive.share_scope import apply_sharing_to_scope, ensure_media_exists

    ensure_media_exists(db, media_id)
    scope = _sharing_scope_out(db, media_id, settings=_settings(request), season=season)
    if scope.file_count == 0:
        raise Problem(422, "Requête invalide", scope.message or "Aucun fichier local.")
    if scope.pull_only:
        raise Problem(422, "Requête invalide", scope.message or "Publication impossible.")
    ruche_ids = _parse_ruche_ids(body.excluded_ruche_ids)
    moment = _now()
    touched = apply_sharing_to_scope(
        db,
        _settings(request),
        media_id,
        season=season,
        exclude_all=body.exclude_all,
        ruche_ids=ruche_ids,
        moment=moment,
    )
    if not touched:
        raise Problem(422, "Requête invalide", "Aucun fichier local dans ce périmètre.")
    db.commit()


@router.get("/media/{media_id}/empreintes", response_model=EmpreintesPageOut)
def get_media_empreintes(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    season: int | None = None,
) -> EmpreintesPageOut:
    del request, _principal
    _require_hive(db)
    return _empreintes_page(db, media_id, season=season)


@router.post("/media/{media_id}/empreintes/verify", response_model=EmpreintesPageOut)
def post_media_empreintes_verify(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    season: int | None = None,
) -> EmpreintesPageOut:
    """Contrôle cohérence des métadonnées d'empreintes (lecture seule)."""
    del request, _principal
    _require_hive(db)
    from mediatracker.hive.fingerprint_details import verify_fingerprint_metadata

    verify_fingerprint_metadata(db, media_id, season=season)
    return _empreintes_page(db, media_id, season=season)


@router.post("/media/{media_id}/empreintes/recalculate", response_model=EmpreintesRecalcOut)
def post_media_empreintes_recalculate(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
    season: int | None = None,
) -> EmpreintesRecalcOut:
    del _principal
    _require_hive(db)
    from mediatracker.hive.fingerprint_details import request_chunk_recalculate
    from mediatracker.hive.share_scope import ensure_media_exists

    ensure_media_exists(db, media_id)
    moment = _now()
    result = request_chunk_recalculate(
        db, _settings(request), media_id, season=season, moment=moment
    )
    db.commit()
    return EmpreintesRecalcOut(
        scheduled_chunks=result.scheduled_chunks,
        in_progress_chunks=result.in_progress_chunks,
        file_sha_queued=result.file_sha_queued,
        skipped_no_intent=result.skipped_no_intent,
        skipped_no_fingerprint=result.skipped_no_fingerprint,
        claims_recovered=result.claims_recovered,
        wake_sent=result.wake_sent,
    )


def _series_follow_ref(work: dict[str, Any]) -> dict[str, Any]:
    episodes = work.get("episodes")
    if isinstance(episodes, list) and episodes:
        return episodes[0]
    if work.get("season") is not None and work.get("episode") is not None:
        return work
    return work


def _ui_follow_existing(db: Session, media: Media, settings: Settings, *, arm_source_poll: bool) -> None:
    """Tout ajout Hive réactive le suivi pour une fiche déjà présente mais non suivie."""
    if media.followed:
        return
    import logging

    logger = logging.getLogger(__name__)
    policy = media.new_episode_policy if media.new_episode_policy in {"auto", "propose"} else None
    if policy is None:
        policy = "auto" if arm_source_poll else "propose"
    try:
        follow(
            db,
            settings.secret_key,
            FollowInput(
                type=media.type,
                title=media.title,
                year=media.year,
                tmdb_id=media.tmdb_id,
                new_episode_policy=policy,
                start_now=arm_source_poll,
            ),
            source="existing",
            media=media,
        )
    except FollowError as exc:
        logger.warning(
            "Réactivation suivi Hive en repli minimal (media_id=%s, code=%s)",
            media.id,
            exc.status,
        )
        media.followed = True
        if media.follow_origin != "jellyfin_import":
            media.follow_origin = "user"


def _ensure_media_for_work(
    db: Session, work: dict[str, Any], settings: Settings, *, arm_source_poll: bool = True
) -> tuple[Media, MediaItem]:
    ref = _series_follow_ref(work)
    item = _local_media_item(db, ref)
    if item is not None:
        media = db.get(Media, item.media_id)
        if media is not None:
            _ui_follow_existing(db, media, settings, arm_source_poll=arm_source_poll)
            if work.get("type") == "series" and work.get("season") is None and work.get("episode") is None:
                return media, item
            return media, item
    title = str(work.get("title") or ref.get("title") or "Œuvre fictive")
    tmdb_id = work.get("tmdb_id")
    media_type = str(work.get("type") or "film")
    if tmdb_id is None:
        raise Problem(422, "Requête invalide", "Identifiant TMDB manquant pour cette œuvre.")
    if media_type == "series" and work.get("season") is None and work.get("episode") is None:
        existing = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == "series").limit(1))
        if existing is not None:
            slot = db.scalar(select(MediaItem).where(MediaItem.media_id == existing.id).limit(1))
            if slot is not None:
                _ui_follow_existing(db, existing, settings, arm_source_poll=arm_source_poll)
                return existing, slot
        try:
            created, _episodes = follow(
                db,
                settings.secret_key,
                FollowInput(
                    type="series",
                    title=title,
                    year=work.get("year") or ref.get("year"),
                    tmdb_id=tmdb_id,
                    poster_url=work.get("poster_url") or ref.get("poster_url"),
                    new_episode_policy="auto" if arm_source_poll else "propose",
                    seasons=[int(ref["season"])] if ref.get("season") is not None else None,
                ),
                source="create",
            )
        except FollowError as exc:
            raise Problem(exc.status, exc.title, exc.detail) from exc
        created.followed = True
        season, episode = ref.get("season"), ref.get("episode")
        slot = None
        if season is not None and episode is not None:
            slot = db.scalar(
                select(MediaItem).where(
                    MediaItem.media_id == created.id,
                    MediaItem.season == season,
                    MediaItem.episode == episode,
                ).limit(1)
            )
        if slot is None:
            slot = db.scalar(select(MediaItem).where(MediaItem.media_id == created.id).limit(1))
        if slot is None:
            raise Problem(500, "Erreur interne", "Impossible de créer l’épisode.")
        return created, slot
    existing = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if existing is not None:
        _ui_follow_existing(db, existing, settings, arm_source_poll=arm_source_poll)
        if media_type == "film":
            slot = db.scalar(select(MediaItem).where(MediaItem.media_id == existing.id).limit(1))
            if slot is None:
                slot = MediaItem(media_id=existing.id, state="missing")
                db.add(slot)
                db.flush()
            return existing, slot
        season, episode = work.get("season"), work.get("episode")
        slot = db.scalar(
            select(MediaItem).where(
                MediaItem.media_id == existing.id,
                MediaItem.season == season,
                MediaItem.episode == episode,
            ).limit(1)
        )
        if slot is None:
            slot = MediaItem(media_id=existing.id, season=season, episode=episode, state="missing")
            db.add(slot)
            db.flush()
        return existing, slot
    try:
        created, _episodes = follow(
            db,
            settings.secret_key,
            FollowInput(
                type=media_type,
                title=title,
                year=work.get("year"),
                tmdb_id=tmdb_id,
                poster_url=work.get("poster_url"),
                new_episode_policy="auto" if arm_source_poll else "propose",
                seasons=[int(work["season"])] if media_type == "series" and work.get("season") is not None else None,
            ),
            source="create",
        )
    except FollowError as exc:
        raise Problem(exc.status, exc.title, exc.detail) from exc
    if media_type == "film":
        slot = db.scalar(select(MediaItem).where(MediaItem.media_id == created.id).limit(1))
    else:
        season, episode = work.get("season"), work.get("episode")
        slot = db.scalar(
            select(MediaItem).where(
                MediaItem.media_id == created.id,
                MediaItem.season == season,
                MediaItem.episode == episode,
            ).limit(1)
        )
    if slot is None:
        raise Problem(500, "Erreur interne", "Impossible de créer l’épisode.")
    created.followed = True
    return created, slot


@router.post("/catalogue/{work_key}/follow", status_code=204)
def post_catalogue_follow(
    work_key: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    work = _work_row(db, settings, work_key)
    if work is None:
        raise Problem(404, "Introuvable", "Œuvre absente du catalogue Hive.")
    media, item = _ensure_media_for_work(db, work, settings)
    from mediatracker.hive.signal_policy import maybe_publish_cherche_on_follow

    maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=_now())
    db.commit()


class CatalogueRequestIn(BaseModel):
    version_key: str | None = None
    season: int | None = None
    episode: int | None = None


@router.post("/catalogue/{work_key}/request")
def post_catalogue_request(
    work_key: str,
    body: CatalogueRequestIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> dict:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    work = _work_row(db, settings, work_key)
    if work is None:
        raise Problem(404, "Introuvable", "Œuvre absente du catalogue Hive.")
    target = work
    if work.get("type") == "series" and isinstance(work.get("episodes"), list):
        if body.season is None or body.episode is None:
            raise Problem(
                422,
                "Requête invalide",
                "Ouvrez la fiche de la série pour choisir un épisode, ou indiquez saison et épisode.",
            )
        picked = pick_episode_work(work, season=int(body.season), episode=int(body.episode))
        if picked is None:
            raise Problem(422, "Requête invalide", "Épisode absent du catalogue Hive.")
        target = picked
    media, item = _ensure_media_for_work(db, target, settings, arm_source_poll=False)
    version_key = body.version_key.strip() if body.version_key else None
    moment = _now()
    result = try_enqueue_hive_for_item(
        db, settings, media=media, item=item, now=moment, version_key=version_key
    )
    db.commit()
    if result.outcome == "miss":
        detail = result.detail
        if not detail:
            from mediatracker.core.profile_attempt_orchestration import load_profile_chain
            from mediatracker.core.profile_fallback import ProfileResolveError
            from mediatracker.hive.source_catalog import catalogue_request_miss_detail

            try:
                profile = load_profile_chain(db, media, settings).principal
            except ProfileResolveError:
                profile = None
            explicit = version_key is not None
            detail = (
                catalogue_request_miss_detail(
                    db,
                    media,
                    item,
                    profile=profile,
                    version_key=version_key,
                    explicit_version=explicit,
                )
                if profile is not None
                else "Aucune annonce Hive pour cette œuvre."
            )
        raise Problem(422, "Requête invalide", detail)
    if result.outcome == "pending":
        raise Problem(409, "Conflit", "Téléchargement Hive en attente (source indisponible).")
    return {"queued": True, "state": result.outcome}


@router.get("/requests/mine")
def get_my_hive_requests(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> dict:
    del _principal
    _require_hive(db)
    from mediatracker.hive.signal_requests import list_my_requests

    rows = list_my_requests(db, _settings(request))
    return {"items": rows}


@router.get("/requests/received")
def get_received_hive_requests(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> dict:
    del _principal
    _require_hive(db)
    from mediatracker.hive.signal_requests import list_received_requests

    rows = list_received_requests(db, _settings(request))
    return {"items": rows}


class HiveRequestActionIn(BaseModel):
    request_id: str = Field(min_length=8, max_length=48)


@router.post("/requests/received/accept", status_code=204)
def post_accept_hive_request(
    body: HiveRequestActionIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    from mediatracker.hive.signal_requests import accept_received_request

    accept_received_request(db, _settings(request), request_id=body.request_id, now=_now())
    db.commit()


@router.post("/requests/received/refuse", status_code=204)
def post_refuse_hive_request(
    body: HiveRequestActionIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    from mediatracker.hive.signal_requests import refuse_received_request

    refuse_received_request(db, _settings(request), request_id=body.request_id, now=_now())
    db.commit()


class HiveChercheConfirmIn(BaseModel):
    media_item_id: int = Field(ge=1)


@router.post("/requests/retract", status_code=204)
def post_retract_hive_cherche(
    body: HiveChercheConfirmIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    """Retire l’annonce cherche (ne plus demander à la ruche)."""
    del _principal
    _require_hive(db)
    item = db.get(MediaItem, body.media_item_id)
    if item is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    media = db.get(Media, item.media_id)
    if media is None or not media.followed:
        raise Problem(422, "Requête invalide", "Suivi requis.")
    from mediatracker.hive.signal_policy import retract_cherche_follow_item

    retract_cherche_follow_item(
        db,
        _settings(request),
        media=media,
        item=item,
        now=_now(),
        remember_user_withdraw=True,
    )
    db.commit()


@router.get("/requests/wanted")
def get_wanted_hive_requests(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> dict:
    del _principal
    _require_hive(db)
    from mediatracker.hive.signal_wanted import list_wanted_requests

    rows = list_wanted_requests(db, _settings(request), now=_now())
    return {"items": rows}


class HiveWantedTakeIn(BaseModel):
    work_key: str = Field(min_length=8, max_length=48)
    release: bool = False


@router.post("/requests/wanted/take", status_code=204)
def post_wanted_hive_take(
    body: HiveWantedTakeIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    del _principal
    _require_hive(db)
    from mediatracker.hive.signal_wanted import take_wanted_request

    take_wanted_request(
        db,
        _settings(request),
        work_key=body.work_key.strip(),
        release=body.release,
        now=_now(),
    )
    db.commit()


@router.post("/requests/announce", status_code=204)
def post_confirm_hive_cherche(
    body: HiveChercheConfirmIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> None:
    """Publication explicite du signal cherche (efface un retrait mémorisé si besoin)."""
    del _principal
    _require_hive(db)
    item = db.get(MediaItem, body.media_item_id)
    if item is None:
        raise Problem(404, "Introuvable", "Épisode introuvable.")
    media = db.get(Media, item.media_id)
    if media is None or not media.followed:
        raise Problem(422, "Requête invalide", "Suivi requis pour annoncer une recherche.")
    from mediatracker.hive.signal_policy import explicit_announce_cherche

    if not explicit_announce_cherche(db, _settings(request), media=media, item=item, now=_now()):
        raise Problem(422, "Requête invalide", "Annonce impossible pour cet épisode.")
    db.commit()

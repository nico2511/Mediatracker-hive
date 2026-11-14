"""Lecture de la vue fusionnée et choix de version (profil, équivalent, disponible)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from mediatracker.db.models import HiveCatalogue, HiveEntry, Media, MediaItem
from mediatracker.hive.hive_catalogue import load_hive_catalogue_rows

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HiveCatalogMatch:
    row: HiveCatalogue
    sources: list[dict[str, Any]]
    sha256_hex: str
    fiche: dict[str, Any]
    cle: dict[str, Any]
    expected_size: int


def _parse_json(text: str) -> dict:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_version_parts(version_key: str) -> tuple[str | None, str | None]:
    if "+" not in version_key:
        return version_key or None, None
    quality, language = version_key.split("+", 1)
    return quality or None, language or None


def _version_rank(
    version_key: str,
    *,
    media: Media,
    allow_fallback: bool,
    quality_fallback: list[str],
    language_fallback: list[str],
    profile,
) -> int | None:
    quality, language = _parse_version_parts(version_key)
    if profile is not None and quality and language:
        from mediatracker.core.quality_profile import (
            ReleaseCandidate,
            acceptance_rank,
            normalize_language,
            normalize_resolution,
        )

        return acceptance_rank(
            ReleaseCandidate(
                language=normalize_language(language),
                resolution=normalize_resolution(quality),
                size=None,
                text=version_key,
            ),
            profile,
            enforce_size=False,
        )
    from mediatracker.core.source_select import variant_rank
    from mediatracker.providers.base import SourceResult

    return variant_rank(
        SourceResult(title=media.title, ref="hive", quality=quality, language=language),
        quality=media.pref_quality,
        language=media.pref_language,
        allow_fallback=allow_fallback,
        quality_fallback=quality_fallback,
        language_fallback=language_fallback,
    )


def _slot_matches_cle(cle: dict, item: MediaItem, media: Media) -> bool:
    if cle.get("tmdb_id") != media.tmdb_id:
        return False
    if cle.get("type") != media.type:
        return False
    if media.type == "film":
        return item.season is None and item.episode is None
    return cle.get("saison") == item.season and cle.get("episode") == item.episode


def _rows_for_item(db: Session, media: Media, item: MediaItem) -> list[HiveCatalogue]:
    out: list[HiveCatalogue] = []
    for row in load_hive_catalogue_rows(db):
        cle = _parse_json(row.cle_json)
        if not _slot_matches_cle(cle, item, media):
            continue
        out.append(row)
    return out


def _expected_size_for_sha(db: Session, sha256_hex: str) -> int:
    from sqlalchemy import select

    entry = db.scalar(
        select(HiveEntry)
        .where(HiveEntry.sha256_hex == sha256_hex, HiveEntry.etat == "disponible")
        .order_by(HiveEntry.id.desc())
        .limit(1)
    )
    if entry is None:
        return 0
    fiche = _parse_json(entry.fiche_json or "")
    for raw in (fiche.get("taille"), fiche.get("size")):
        if isinstance(raw, int) and raw > 0:
            return raw
    payload = _parse_json(entry.payload_json or "")
    for raw in (payload.get("taille"), payload.get("size")):
        if isinstance(raw, int) and raw > 0:
            return raw
    return 0


def _fiche_for_match(db: Session, cle: dict, sha256_hex: str, media: Media, item: MediaItem) -> dict:
    from sqlalchemy import select

    entry = db.scalar(
        select(HiveEntry)
        .where(HiveEntry.sha256_hex == sha256_hex, HiveEntry.etat == "disponible")
        .order_by(HiveEntry.id.desc())
        .limit(1)
    )
    if entry is not None and entry.fiche_json:
        fiche = _parse_json(entry.fiche_json)
        if fiche:
            return fiche
    taille = _expected_size_for_sha(db, sha256_hex)
    doc: dict = {
        "type": media.type,
        "tmdb_id": media.tmdb_id,
        "title": media.title,
        "year": media.year,
        "season": item.season,
        "episode": item.episode,
    }
    if taille > 0:
        doc["taille"] = taille
    return doc


UNKNOWN_VERSION_RANK = 50_000


def rank_catalog_version_for_profile_attempt(
    version_key: str,
    *,
    profile,
    size: int | None = None,
) -> int | None:
    """Rank one catalogue version for a single explicit profile (no fiche prefs, no CSV).

    Exclusions ``exclude_keywords`` use only ``version_key`` (no release filename on Hive rows).
    
    ``unknown+unknown`` (métadonnées release absentes) reste éligible avec une pénalité fixe,
    sans élargissement via les préférences de la fiche.
    """
    quality, language = _parse_version_parts(version_key)
    if not quality or not language:
        return None
    from mediatracker.core.quality_profile import (
        ReleaseCandidate,
        acceptance_rank,
        normalize_language,
        normalize_resolution,
        profile_has_size_bounds,
    )

    if quality.casefold() == "unknown" and language.casefold() == "unknown":
        from mediatracker.hive.catalog_validity import UNKNOWN_LEGACY_SUSPECT_BYTES

        has_size_bounds = profile_has_size_bounds(profile)
        size_known = size is not None and size > 0
        if size_known and int(size) < UNKNOWN_LEGACY_SUSPECT_BYTES:
            return None
        if has_size_bounds and not size_known:
            return None
        return UNKNOWN_VERSION_RANK

    normalized_language = normalize_language(language)
    normalized_resolution = normalize_resolution(quality)
    if normalized_language is None:
        return None
    if normalized_resolution is None and (
        profile.min_resolution is not None or profile.max_resolution is not None
    ):
        return None
    has_size_bounds = profile_has_size_bounds(profile)
    size_known = size is not None and size > 0
    if has_size_bounds and not size_known:
        return None
    return acceptance_rank(
        ReleaseCandidate(
            language=normalized_language,
            resolution=normalized_resolution,
            size=size,
            text=version_key,
        ),
        profile,
        enforce_size=has_size_bounds and size_known,
    )


def pick_catalog_match_for_profile_attempt(
    db: Session,
    media: Media,
    item: MediaItem,
    *,
    profile,
    prefer_available: bool = True,
    version_key: str | None = None,
    skip_bitrate_sanity: bool = False,
) -> HiveCatalogMatch | None:
    """Catalogue match for one explicit profile (orchestrator / lot C entry point)."""
    candidates = _rows_for_item(db, media, item)
    if version_key:
        vk = version_key.strip()
        candidates = [row for row in candidates if row.version_key == vk]
    if not candidates:
        return None
    ranked: list[tuple[int, HiveCatalogue]] = []
    for row in candidates:
        sha = ""
        try:
            sources = json.loads(row.sources_json)
        except json.JSONDecodeError:
            sources = []
        if isinstance(sources, list):
            for src in sources:
                if isinstance(src, dict) and isinstance(src.get("sha256"), str) and src["sha256"]:
                    sha = src["sha256"]
                    break
        size = _expected_size_for_sha(db, sha) if sha else 0
        if not skip_bitrate_sanity and size > 0:
            from mediatracker.core.download_bitrate_sanity import (
                bitrate_sanity_reject_reason,
                playback_duration_seconds,
            )

            quality, _language = _parse_version_parts(row.version_key)
            duration = playback_duration_seconds(media, item)
            reason = bitrate_sanity_reject_reason(
                size=size,
                quality=quality,
                duration_sec=duration,
            )
            if reason is not None:
                logger.info("%s — qualité=%s, taille=%s o", reason, quality or "?", size)
                continue
        rank = rank_catalog_version_for_profile_attempt(
            row.version_key,
            profile=profile,
            size=size if size > 0 else None,
        )
        if rank is None:
            continue
        penalty = rank
        if prefer_available and not row.disponible:
            penalty += 10_000
        ranked.append((penalty, row))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    chosen = ranked[0][1]
    try:
        sources = json.loads(chosen.sources_json)
    except json.JSONDecodeError:
        sources = []
    if not isinstance(sources, list):
        sources = []
    sources = [dict(s) for s in sources if isinstance(s, dict)]
    sha = ""
    for src in sources:
        if isinstance(src.get("sha256"), str) and src["sha256"]:
            sha = src["sha256"]
            break
    cle = _parse_json(chosen.cle_json)
    if not sha:
        return None
    fiche = _fiche_for_match(db, cle, sha, media, item)
    size = _expected_size_for_sha(db, sha)
    if size <= 0:
        for raw in (fiche.get("taille"), fiche.get("size")):
            if isinstance(raw, int) and raw > 0:
                size = raw
                break
    return HiveCatalogMatch(
        row=chosen,
        sources=sources,
        sha256_hex=sha,
        fiche=fiche,
        cle=cle,
        expected_size=size,
    )


def _bitrate_miss_message(
    db: Session,
    media: Media,
    item: MediaItem,
    row: HiveCatalogue,
) -> str | None:
    sha = _sha_for_catalogue_row(row)
    size = _expected_size_for_sha(db, sha) if sha else 0
    if size <= 0:
        return None
    from mediatracker.core.download_bitrate_sanity import (
        bitrate_sanity_reject_reason,
        playback_duration_seconds,
    )

    quality, _language = _parse_version_parts(row.version_key)
    duration = playback_duration_seconds(media, item)
    reason = bitrate_sanity_reject_reason(size=size, quality=quality, duration_sec=duration)
    if reason is None:
        return None
    if "trop élevé" in reason:
        return (
            f"La version « {row.version_key} » est signalée comme disproportionnée "
            f"(taille par rapport à la qualité annoncée)."
        )
    if "trop faible" in reason:
        return (
            f"La version « {row.version_key} » est signalée comme trop légère "
            f"(taille par rapport à la qualité annoncée)."
        )
    return f"La version « {row.version_key} » ne passe pas le contrôle de taille pour cette qualité."


def catalogue_request_miss_detail(
    db: Session,
    media: Media,
    item: MediaItem,
    *,
    profile,
    version_key: str | None = None,
    explicit_version: bool = False,
) -> str:
    """Message HTTP distinct quand ``try_enqueue_hive_for_item`` renvoie ``miss``."""
    if item.watched:
        return "Cette version est déjà marquée comme vue."
    from mediatracker.core.file_status import FileStatusIndex

    file_status = FileStatusIndex.load(db, [item]).for_item(item)
    if file_status == "present" or (item.downloaded and item.file_path):
        return "Cette œuvre est déjà dans votre bibliothèque."
    rows = _rows_for_item(db, media, item)
    if not rows:
        return "Aucune annonce Hive pour cette œuvre."
    vk = version_key.strip() if isinstance(version_key, str) and version_key.strip() else None
    if vk:
        matched = [row for row in rows if row.version_key == vk]
        if not matched:
            return f"La version « {vk} » est absente du catalogue Hive."
        rows = matched
    for row in rows:
        if not _sha_for_catalogue_row(row):
            return f"La version « {row.version_key} » n'a pas d'empreinte SHA exploitable dans le catalogue."
    ranked = False
    any_disponible = False
    for row in rows:
        if row.disponible:
            any_disponible = True
        size = _expected_size_for_sha(db, _sha_for_catalogue_row(row))
        if rank_catalog_version_for_profile_attempt(
            row.version_key, profile=profile, size=size if size > 0 else None
        ) is not None:
            ranked = True
    if not ranked:
        return "Les versions Hive ne correspondent pas à votre profil qualité."
    if not any_disponible:
        return "Les sources Hive sont injoignables pour l'instant."
    if not explicit_version:
        for row in rows:
            bitrate_msg = _bitrate_miss_message(db, media, item, row)
            if bitrate_msg is not None:
                return bitrate_msg
    if not explicit_version:
        from sqlalchemy import select

        from mediatracker.core.force_download import IN_PROGRESS
        from mediatracker.core.profile_attempt_orchestration import active_hive_fetch_sha_for_item
        from mediatracker.db.models import Download

        if item.id is not None and db.scalar(
            select(Download.id).where(
                Download.media_item_id == item.id,
                Download.state.in_(IN_PROGRESS),
            )
        ):
            for row in rows:
                bitrate_msg = _bitrate_miss_message(db, media, item, row)
                if bitrate_msg is not None:
                    return bitrate_msg
            return (
                "Un téléchargement classique est déjà en cours pour cette œuvre ; "
                "choisissez une version précise dans le catalogue pour basculer sur Hive."
            )
        frozen = active_hive_fetch_sha_for_item(db, media, item)
        if frozen:
            target_sha = next((_sha_for_catalogue_row(row) for row in rows if _sha_for_catalogue_row(row)), "")
            if target_sha and target_sha != frozen:
                return "Un transfert Hive est déjà en cours pour une autre version de cette œuvre."
    return "Aucune version Hive utilisable pour cette œuvre."


def _sha_for_catalogue_row(row: HiveCatalogue) -> str:
    try:
        sources = json.loads(row.sources_json)
    except json.JSONDecodeError:
        sources = []
    if isinstance(sources, list):
        for src in sources:
            if isinstance(src, dict) and isinstance(src.get("sha256"), str) and src["sha256"]:
                return src["sha256"]
    return ""


def catalog_sha_on_item_matches_profile(
    db: Session,
    media: Media,
    item: MediaItem,
    sha256_hex: str,
    *,
    profile,
) -> bool:
    """Vérifie le SHA catalogue exact sur le slot (sans re-classement global)."""
    want = str(sha256_hex).lower()
    for row in _rows_for_item(db, media, item):
        sha = _sha_for_catalogue_row(row)
        if not sha or str(sha).lower() != want:
            continue
        size = _expected_size_for_sha(db, sha)
        rank = rank_catalog_version_for_profile_attempt(
            row.version_key,
            profile=profile,
            size=size if size > 0 else None,
        )
        if rank is not None:
            return True
    return False


def pick_catalog_match(
    db: Session,
    media: Media,
    item: MediaItem,
    *,
    allow_fallback: bool,
    quality_fallback: list[str],
    language_fallback: list[str],
    profile,
    prefer_available: bool,
) -> HiveCatalogMatch | None:
    """Choisit une version pour l'épisode ; None si absent du catalogue."""
    candidates = _rows_for_item(db, media, item)
    if not candidates:
        return None
    ranked: list[tuple[int, HiveCatalogue]] = []
    for row in candidates:
        rank = _version_rank(
            row.version_key,
            media=media,
            allow_fallback=allow_fallback,
            quality_fallback=quality_fallback,
            language_fallback=language_fallback,
            profile=profile,
        )
        if rank is None:
            continue
        penalty = rank
        if prefer_available and not row.disponible:
            penalty += 10_000
        ranked.append((penalty, row))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    chosen = ranked[0][1]
    try:
        sources = json.loads(chosen.sources_json)
    except json.JSONDecodeError:
        sources = []
    if not isinstance(sources, list):
        sources = []
    sources = [dict(s) for s in sources if isinstance(s, dict)]
    sha = ""
    for src in sources:
        if isinstance(src.get("sha256"), str) and src["sha256"]:
            sha = src["sha256"]
            break
    cle = _parse_json(chosen.cle_json)
    if not sha:
        return None
    fiche = _fiche_for_match(db, cle, sha, media, item)
    size = _expected_size_for_sha(db, sha)
    if size <= 0:
        for raw in (fiche.get("taille"), fiche.get("size")):
            if isinstance(raw, int) and raw > 0:
                size = raw
                break
    return HiveCatalogMatch(
        row=chosen,
        sources=sources,
        sha256_hex=sha,
        fiche=fiche,
        cle=cle,
        expected_size=size,
    )

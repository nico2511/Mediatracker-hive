"""Validation des annonces catalogue Hive (publication locale et fusion distante)."""

from __future__ import annotations

import json
from typing import Any

from mediatracker.db.models import HiveEntry, HiveFileFingerprint, Media, MediaItem

# Annonces « unknown+unknown » au-delà de cette taille restent soumises au contrôle débit.
UNKNOWN_LEGACY_SUSPECT_BYTES = 256 * 1024

# En dessous : manifestement pas une vidéo complète (règle catalogue).
MIN_PLAUSIBLE_VIDEO_BYTES = 512


def _parse_version_parts(version_key: str) -> tuple[str, str]:
    if "+" not in version_key:
        return version_key.strip().casefold(), ""
    q, lang = version_key.split("+", 1)
    return q.strip().casefold(), lang.strip().casefold()


def version_parts_from_cle(cle: dict[str, Any]) -> tuple[str, str]:
    return _parse_version_parts(str(cle.get("version") or ""))


def announced_size_from_parts(
    *,
    fiche: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> int | None:
    for doc in (fiche, payload):
        if not isinstance(doc, dict):
            continue
        for key in ("taille", "size"):
            raw = doc.get(key)
            if isinstance(raw, int) and raw > 0:
                return raw
    return None


def announced_size_for_entry(row: HiveEntry) -> int | None:
    fiche: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    if row.fiche_json:
        try:
            parsed = json.loads(row.fiche_json)
            if isinstance(parsed, dict):
                fiche = parsed
        except json.JSONDecodeError:
            pass
    if row.payload_json:
        try:
            parsed = json.loads(row.payload_json)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            pass
    return announced_size_from_parts(fiche=fiche, payload=payload)


def _unknown_version_too_small(size: int | None) -> bool:
    if size is None or size <= 0:
        return False
    return size < UNKNOWN_LEGACY_SUSPECT_BYTES


def remote_catalog_offer_plausible(
    *,
    cle: dict[str, Any],
    fiche: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    sha256_hex: str | None,
    etat: str,
) -> bool:
    """Filtre les payloads gossip manifestement invalides (ne modifie pas le disque)."""
    if etat != "disponible":
        return True
    if not isinstance(sha256_hex, str) or len(sha256_hex) != 64:
        return False
    size = announced_size_from_parts(fiche=fiche if isinstance(fiche, dict) else None, payload=payload)
    if size is not None and size < MIN_PLAUSIBLE_VIDEO_BYTES:
        return False
    quality, language = version_parts_from_cle(cle)
    if quality == "unknown" and language == "unknown" and _unknown_version_too_small(size):
        return False
    return True


def local_offer_publishable(
    *,
    media: Media,
    item: MediaItem,
    fp: HiveFileFingerprint | None,
    fingerprint_hex: str,
) -> bool:
    """Refuse une publication locale si le fichier ou l'empreinte ne correspondent plus."""
    from mediatracker.hive.catalog import (  # import tardif (évite cycle)
        _fingerprint_matches_present_file,
    )

    if fp is None or not fp.sha256_hex:
        return False
    if fingerprint_hex.lower() != fp.sha256_hex.lower():
        return False
    if not _fingerprint_matches_present_file(item, fp):
        return False
    size = int(fp.size or 0)
    if size < MIN_PLAUSIBLE_VIDEO_BYTES:
        return False
    quality = (item.release_quality or "").strip().casefold()
    language = (item.release_language or "").strip().casefold()
    if quality == "unknown" and language == "unknown" and size < UNKNOWN_LEGACY_SUSPECT_BYTES:
        from mediatracker.core.download_bitrate_sanity import (
            bitrate_sanity_reject_reason,
            playback_duration_seconds,
        )

        duration = playback_duration_seconds(media, item)
        if bitrate_sanity_reject_reason(size=size, quality=None, duration_sec=duration) is not None:
            return False
    return True

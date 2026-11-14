"""Reject download candidates whose size is absurd for known duration and quality.

Applied before final choice across sources when ``size`` is known. Unknown size
or duration skips filtering (duration falls back to prudent defaults when missing).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from mediatracker.core.quality_profile import normalize_resolution
from mediatracker.db.models import Media, MediaItem
from mediatracker.library.parse import parse_release_slot
from mediatracker.providers.base import LinkCandidate

logger = logging.getLogger(__name__)

DEFAULT_FILM_MINUTES = 110
DEFAULT_EPISODE_MINUTES = 45

# (min_bytes_per_sec, max_bytes_per_sec) per nominal resolution — wide, absurd-only.
BITRATE_BOUNDS_BPS: dict[str, tuple[int, int]] = {
    "480p": (10_000, 15_000_000),
    "720p": (10_000, 25_000_000),
    "1080p": (10_000, 25_000_000),
    "2160p": (20_000, 50_000_000),
}
UNKNOWN_QUALITY_BOUNDS = (10_000, 50_000_000)

_RESOLUTION_BUCKET = {
    "SD": "480p",
    "HD": "720p",
    "HDLight": "720p",
    "1080p": "1080p",
    "4K": "2160p",
}


def quality_bitrate_bucket(quality: str | None) -> str | None:
    normalized = normalize_resolution(quality)
    if normalized is None:
        return None
    return _RESOLUTION_BUCKET.get(normalized)


def episode_count_in_release(filename: str | None) -> int:
    slot = parse_release_slot(filename or "")
    if slot.episode is None:
        return 1
    if slot.episode_end is not None and slot.episode_end >= slot.episode:
        return slot.episode_end - slot.episode + 1
    return 1


def playback_duration_seconds(
    media: Media,
    item: MediaItem,
    *,
    filename: str | None = None,
) -> float:
    """Duration used for bitrate checks (seconds)."""
    minutes: int | None = None
    if media.tmdb_runtime_min is not None and media.tmdb_runtime_min > 0:
        minutes = int(media.tmdb_runtime_min)
    if media.type == "film":
        if minutes is None:
            minutes = DEFAULT_FILM_MINUTES
        return float(minutes) * 60.0
    if minutes is None:
        minutes = DEFAULT_EPISODE_MINUTES
    episode_slots = episode_count_in_release(filename)
    return float(minutes) * 60.0 * float(episode_slots)


def bitrate_sanity_reject_reason(
    *,
    size: int | None,
    quality: str | None,
    duration_sec: float,
) -> str | None:
    if size is None or size <= 0:
        return None
    if duration_sec <= 0:
        return None
    bps = float(size) / float(duration_sec)
    bucket = quality_bitrate_bucket(quality)
    if bucket is None:
        lo, hi = UNKNOWN_QUALITY_BOUNDS
    else:
        lo, hi = BITRATE_BOUNDS_BPS[bucket]
    label = quality or "?"
    if bps < lo:
        return f"taille suspecte : débit trop faible ({label}, {size} o)"
    if bps > hi:
        return f"taille suspecte : débit trop élevé ({label}, {size} o)"
    return None


def _log_reject(reason: str, *, size: int, quality: str | None) -> None:
    logger.info("%s — qualité=%s, taille=%s o", reason, quality or "?", size)


def filter_link_candidates(
    media: Media,
    item: MediaItem,
    candidates: Sequence[LinkCandidate],
) -> list[LinkCandidate]:
    kept: list[LinkCandidate] = []
    for candidate in candidates:
        if candidate.size is None or candidate.size <= 0:
            kept.append(candidate)
            continue
        duration = playback_duration_seconds(media, item, filename=candidate.filename)
        reason = bitrate_sanity_reject_reason(
            size=candidate.size,
            quality=candidate.quality,
            duration_sec=duration,
        )
        if reason is None:
            kept.append(candidate)
            continue
        _log_reject(reason, size=candidate.size, quality=candidate.quality)
    return kept


def filter_stored_links(
    media: Media,
    item: MediaItem,
    links: Sequence,
) -> list:
    kept = []
    for link in links:
        size = getattr(link, "size", None)
        if not isinstance(size, int) or size <= 0:
            kept.append(link)
            continue
        filename = getattr(link, "filename", None)
        duration = playback_duration_seconds(media, item, filename=filename)
        quality = getattr(link, "quality", None)
        reason = bitrate_sanity_reject_reason(size=size, quality=quality, duration_sec=duration)
        if reason is None:
            kept.append(link)
            continue
        _log_reject(reason, size=int(size), quality=quality)
    return kept

"""États carte catalogue Hive (agrégation séries, pastilles UX)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Download, DownloadHiveFetch, HiveFileFingerprint, Media, MediaItem
from mediatracker.hive.catalog import _gossip_poster_url, item_is_shareable_base
from mediatracker.hive.download_aria2 import hive_download_active_for_item
from mediatracker.hive.share_policy import effective_exclude_all_for_ui, has_share_intent

CardState = Literal[
    "error",
    "transfer",
    "preparing",
    "library",
    "waiting",
    "available",
    "unavailable",
]

CardTone = Literal["danger", "honey", "muted", "info", "warn", "ok"]

CARD_STATE_PRIORITY: tuple[CardState, ...] = (
    "error",
    "transfer",
    "preparing",
    "library",
    "waiting",
    "available",
    "unavailable",
)

_STATE_LABELS: dict[CardState, tuple[str, str]] = {
    "error": ("Erreur", "Erreur"),
    "transfer": ("Téléchargement", "Tél."),
    "preparing": ("Préparation au partage", "Prép."),
    "library": ("Dans ma bibliothèque", "Bibliothèque"),
    "waiting": ("En attente", "En attente"),
    "available": ("Disponible", "Dispo."),
    "unavailable": ("Indisponible pour l'instant", "Indispo."),
}

_TONE_BY_STATE: dict[CardState, CardTone] = {
    "error": "danger",
    "transfer": "honey",
    "preparing": "muted",
    "library": "info",
    "waiting": "warn",
    "available": "ok",
    "unavailable": "muted",
}

_ACTIVE_DOWNLOAD_STATES = frozenset({"pending", "resolving", "queued", "downloading", "post_processing"})


def stable_series_work_key(tmdb_id: int) -> str:
    raw = json.dumps(("series", tmdb_id), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _parse_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _priority_index(state: CardState) -> int:
    try:
        return CARD_STATE_PRIORITY.index(state)
    except ValueError:
        return len(CARD_STATE_PRIORITY)


def _merge_versions(version_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for versions in version_lists:
        for ver in versions:
            if not isinstance(ver, dict):
                continue
            vk = str(ver.get("version_key") or "")
            prev = merged.get(vk)
            if prev is None:
                merged[vk] = dict(ver)
                merged[vk]["version_key"] = vk
                merged[vk]["disponible"] = bool(ver.get("disponible"))
                merged[vk]["source_count"] = int(ver.get("source_count") or 0)
                merged[vk]["recommended"] = bool(ver.get("recommended"))
            else:
                prev["disponible"] = prev["disponible"] or bool(ver.get("disponible"))
                prev["source_count"] = max(int(prev["source_count"]), int(ver.get("source_count") or 0))
                prev["recommended"] = bool(prev.get("recommended")) or bool(ver.get("recommended"))
                for key in ("quality", "language"):
                    if prev.get(key) is None and ver.get(key) is not None:
                        prev[key] = ver.get(key)
                for key in ("size_bytes", "ruche_count"):
                    cur = prev.get(key)
                    nxt = ver.get(key)
                    if isinstance(nxt, int) and nxt > 0:
                        if not isinstance(cur, int) or cur <= 0:
                            prev[key] = nxt
                        elif key == "size_bytes":
                            prev[key] = max(cur, nxt)
                        else:
                            prev[key] = max(cur, nxt)
    return list(merged.values())


def catalogue_work_is_fully_owned(work: dict[str, Any]) -> bool:
    """True si l’œuvre (série : tous les épisodes catalogue) a déjà un fichier local présent."""
    episodes = work.get("episodes")
    if work.get("type") == "series" and isinstance(episodes, list) and episodes:
        return all(bool(ep.get("in_library")) for ep in episodes if isinstance(ep, dict))
    return bool(work.get("in_library"))


def aggregate_catalogue_works(
    db: Session,
    settings: Settings,
    works: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fusionne les épisodes de série en une ligne ; enrichit les films inchangés."""
    del db, settings
    films: list[dict[str, Any]] = []
    series_buckets: dict[int, list[dict[str, Any]]] = {}
    for work in works:
        if work.get("type") != "series":
            films.append(dict(work))
            continue
        tmdb_id = work.get("tmdb_id")
        if tmdb_id is None:
            films.append(dict(work))
            continue
        series_buckets.setdefault(int(tmdb_id), []).append(dict(work))

    out: list[dict[str, Any]] = list(films)
    for tmdb_id, episodes in series_buckets.items():
        episodes.sort(key=lambda w: (w.get("season") or 0, w.get("episode") or 0))
        head = episodes[0]
        series_posters = sorted(
            p
            for ep in episodes
            if (p := _gossip_poster_url(ep.get("poster_url") if isinstance(ep.get("poster_url"), str) else None))
        )
        merged = {
            "work_key": stable_series_work_key(tmdb_id),
            "type": "series",
            "tmdb_id": tmdb_id,
            "season": None,
            "episode": None,
            "title": head.get("title") or "Œuvre fictive",
            "year": head.get("year"),
            "poster_url": series_posters[0] if series_posters else _gossip_poster_url(head.get("poster_url")),
            "overview": head.get("overview"),
            "disponible": any(bool(ep.get("disponible")) for ep in episodes),
            "in_library": any(bool(ep.get("in_library")) for ep in episodes),
            "media_id": head.get("media_id"),
            "media_item_id": None,
            "versions": _merge_versions([ep.get("versions") or [] for ep in episodes]),
            "episode_count": len(episodes),
            "episodes": episodes,
        }
        out.append(merged)
    return out


def _download_progress_pct(row: Download) -> float | None:
    total = max(int(row.size or 0), 1)
    done = max(0, int(row.progress or 0))
    return min(100.0, round(100.0 * done / total, 1))


def _active_hive_download(
    db: Session, item: MediaItem, sha256_hex: str
) -> Download | None:
    if item.id is None or not sha256_hex:
        return None
    needle = str(sha256_hex).lower()
    return db.scalar(
        select(Download)
        .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
        .where(
            Download.media_item_id == item.id,
            DownloadHiveFetch.sha256_hex == needle,
            Download.state.in_(tuple(_ACTIVE_DOWNLOAD_STATES)),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )


def _preparing_progress(db: Session, item: MediaItem | None) -> float | None:
    if item is None or item.id is None or not item.file_path:
        return None
    fp = db.get(HiveFileFingerprint, item.id)
    if fp is None or (fp.sha256_hex or "").strip():
        return None
    total = int(fp.size or 0)
    if total <= 0:
        return None
    partial = int(fp.partial_size or 0)
    return min(100.0, round(100.0 * partial / total, 1))


def _resolve_item(db: Session, work: dict[str, Any]) -> MediaItem | None:
    item_id = work.get("media_item_id")
    if item_id is not None:
        return db.get(MediaItem, int(item_id))
    return None


def _resolve_media(db: Session, work: dict[str, Any]) -> Media | None:
    media_id = work.get("media_id")
    if media_id is not None:
        return db.get(Media, int(media_id))
    tmdb_id = work.get("tmdb_id")
    media_type = work.get("type")
    if tmdb_id is None or media_type is None:
        return None
    return db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))


def _card_state_for_work(
    db: Session, settings: Settings, work: dict[str, Any]
) -> tuple[CardState, float | None, str | None]:
    del settings
    item = _resolve_item(db, work)

    error_line: str | None = None
    progress: float | None = None

    if item is not None and item.id is not None:
        failed = db.scalar(
            select(Download)
            .join(DownloadHiveFetch, DownloadHiveFetch.download_id == Download.id)
            .where(
                Download.media_item_id == item.id,
                Download.state == "failed",
                Download.error_code.is_not(None),
            )
            .order_by(Download.id.desc())
            .limit(1)
        )
        if failed is not None and (failed.error or "").strip():
            error_line = (failed.error or "").strip() or "Échec du téléchargement Hive."
            return "error", None, error_line

        fp = db.get(HiveFileFingerprint, item.id)
        sha = (fp.sha256_hex or "") if fp else ""
        active = _active_hive_download(db, item, sha) if sha else None
        if active is None and fp is not None:
            for ver in work.get("versions") or []:
                if not isinstance(ver, dict):
                    continue
                vsha = str(ver.get("sha256_hex") or ver.get("sha256") or "").lower()
                if vsha and hive_download_active_for_item(db, item, vsha):
                    active = _active_hive_download(db, item, vsha)
                    break
        if active is not None:
            progress = _download_progress_pct(active)
            return "transfer", progress, None

        preparing = fp is not None and not (fp.sha256_hex or "").strip() and bool(item.file_path)
        if preparing:
            progress = _preparing_progress(db, item)
            return "preparing", progress, None

    if work.get("in_library"):
        return "library", None, None

    media = _resolve_media(db, work)
    followed = bool(media and media.followed)
    if followed and not work.get("disponible"):
        return "waiting", None, None

    if work.get("disponible"):
        return "available", None, None

    return "unavailable", None, None


def compute_card_fields(
    db: Session,
    settings: Settings,
    work_dict: dict[str, Any],
) -> dict[str, Any]:
    episodes = work_dict.get("episodes")
    if isinstance(episodes, list) and episodes:
        episode_fields = [compute_card_fields(db, settings, ep) for ep in episodes if isinstance(ep, dict)]
        best = min(episode_fields, key=lambda f: _priority_index(f["card_state"]))
        followed = any(bool(f.get("followed")) for f in episode_fields)
        shared_by_me = any(bool(f.get("shared_by_me")) for f in episode_fields)
        on_disk = any(bool(f.get("on_disk")) for f in episode_fields)
        out = dict(best)
        out["followed"] = followed
        out["shared_by_me"] = shared_by_me
        out["on_disk"] = on_disk
        return out

    state, progress, error_line = _card_state_for_work(db, settings, work_dict)
    full_label, short_label = _STATE_LABELS[state]
    item = _resolve_item(db, work_dict)
    media = _resolve_media(db, work_dict)
    followed = bool(media and media.followed)
    on_disk = bool(work_dict.get("in_library"))
    shared_by_me = False
    if item is not None and item.id is not None:
        exclude_all_ui = effective_exclude_all_for_ui(db, item.id)
        shareable = item_is_shareable_base(db, item, hive_enabled=True)
        shared_by_me = shareable and not exclude_all_ui and has_share_intent(db, item.id)
    return {
        "card_state": state,
        "state_label": full_label,
        "state_label_short": short_label,
        "progress_pct": progress,
        "error_line": error_line,
        "followed": followed,
        "shared_by_me": shared_by_me,
        "on_disk": on_disk,
        "tone": _TONE_BY_STATE[state],
    }


def pick_episode_work(work: dict[str, Any], *, season: int, episode: int) -> dict[str, Any] | None:
    episodes = work.get("episodes")
    if not isinstance(episodes, list):
        return None
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        if ep.get("season") == season and ep.get("episode") == episode:
            return ep
    return None

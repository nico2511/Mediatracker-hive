"""Journal dédoublonné par source pour chaque tentative de téléchargement."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.source_state import _source_label
from mediatracker.db.models import Download, Event, Link, Media, MediaItem
from mediatracker.events import record_event
from mediatracker.providers.downloaders.aria2 import scrub_url

_manual_link_logger = logging.getLogger("mediatracker.core.pipeline.manual_link")

MANUAL_LINK_USED_JOURNAL = (
    "Lien manuel utilisé (sans restriction : profil, taille et ordre hébergeur ignorés)"
)

_SOURCE_ATTEMPT_TYPE = "download.source_attempt"
_MANUAL_LINK_ATTEMPT_TYPE = "download.manual_link"
_MANUAL_SOURCE_PAGE_TYPE = "download.manual_source_page"
_ISSUES = frozenset({"found", "empty", "refused", "skipped", "error"})


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _fingerprint(payload: dict[str, Any]) -> str:
    return f"{payload.get('issue')}|{payload.get('reason', '')}"


def format_source_attempt_message(
    source: str,
    *,
    issue: str,
    reason: str | None = None,
    link_count: int | None = None,
) -> str:
    label = _source_label(source)
    if issue == "found":
        count = 0 if link_count is None else max(0, int(link_count))
        suffix = "lien trouvé" if count == 1 else "liens trouvés"
        return f"{label} : {count} {suffix}."
    if issue == "empty":
        return f"{label} : aucun résultat."
    if issue == "refused":
        detail = (reason or "filtre profil ou qualité").strip()
        return f"{label} : refusé ({detail})."
    if issue == "skipped":
        detail = (reason or "source non disponible").strip()
        return f"{label} : ignoré ({detail})."
    if issue == "error":
        detail = (reason or "erreur").strip()
        return f"{label} : erreur ({detail})."
    return f"{label} : {issue}."


def log_manual_link_used(media: Media, item: MediaItem, link: Link) -> None:
    hoster = (link.hoster or "").strip() or "—"
    _manual_link_logger.info(
        "lien manuel utilisé sans restriction (profil, taille, hébergeur ignorés) : "
        "media=%s item=%s link=%s hébergeur=%s",
        media.id,
        item.id,
        link.id,
        hoster,
    )


def log_manual_link_failed(
    media: Media,
    item: MediaItem,
    link: Link | None,
    *,
    reason: str,
) -> None:
    hoster = (link.hoster or "").strip() if link is not None else "—"
    link_id = link.id if link is not None else "—"
    detail = scrub_url(reason or "erreur").strip()[:300]
    _manual_link_logger.warning(
        "lien manuel en échec (sans restriction profil/taille/hébergeur) : "
        "media=%s item=%s link=%s hébergeur=%s raison=%s",
        media.id,
        item.id,
        link_id,
        hoster or "—",
        detail,
    )


def record_manual_link_used(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    *,
    link: Link | None = None,
    now: datetime | None = None,
) -> Event:
    moment = _aware(now or datetime.now(timezone.utc))
    message = MANUAL_LINK_USED_JOURNAL
    if link is not None:
        log_manual_link_used(media, item, link)
    payload: dict[str, Any] = {
        "download_id": row.id,
        "message": message,
        "title": media.title,
        "season": item.season,
        "episode": item.episode,
        "issue": "found",
    }
    dedupe_key = f"{_MANUAL_LINK_ATTEMPT_TYPE}:{row.id}:used"
    existing = db.scalar(
        select(Event).where(Event.type == _MANUAL_LINK_ATTEMPT_TYPE, Event.dedupe_key == dedupe_key)
    )
    if existing is not None:
        if existing.payload == payload:
            return existing
        existing.payload = payload
        existing.level = "info"
        existing.ts = moment
        existing.last_seen_at = moment
        db.flush()
        return existing
    return record_event(
        db,
        _MANUAL_LINK_ATTEMPT_TYPE,
        level="info",
        media_id=media.id,
        item_id=item.id,
        payload=payload,
        dedupe_key=dedupe_key,
        when=moment,
    )


def _manual_source_page_used_message(profile_name: str, *, profile_rank: int) -> str:
    name = profile_name.strip() or "Profil"
    if profile_rank <= 1:
        return f"Page source manuelle : lien utilisé (profil « {name} »)"
    return f"Page source manuelle : lien utilisé (repli « {name} », rang {profile_rank})"


def record_manual_source_page_used(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    *,
    profile_name: str,
    profile_rank: int = 1,
    now: datetime | None = None,
) -> Event:
    moment = _aware(now or datetime.now(timezone.utc))
    message = _manual_source_page_used_message(profile_name, profile_rank=profile_rank)
    payload: dict[str, Any] = {
        "download_id": row.id,
        "message": message,
        "profile_name": profile_name,
        "profile_rank": profile_rank,
        "title": media.title,
        "season": item.season,
        "episode": item.episode,
        "issue": "found",
    }
    dedupe_key = f"{_MANUAL_SOURCE_PAGE_TYPE}:{row.id}:used"
    return record_event(
        db,
        _MANUAL_SOURCE_PAGE_TYPE,
        level="info",
        media_id=media.id,
        item_id=item.id,
        payload=payload,
        dedupe_key=dedupe_key,
        when=moment,
    )


def record_manual_source_page_empty(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    *,
    reason: str,
    now: datetime | None = None,
) -> Event:
    moment = _aware(now or datetime.now(timezone.utc))
    detail = reason.strip()
    if detail == "episode":
        message = "Page source manuelle : aucun lien pour l'épisode"
    else:
        message = "Page source manuelle : aucun lien ne correspond au profil"
    payload: dict[str, Any] = {
        "download_id": row.id,
        "message": message,
        "reason": detail,
        "title": media.title,
        "season": item.season,
        "episode": item.episode,
        "issue": "empty",
    }
    dedupe_key = f"{_MANUAL_SOURCE_PAGE_TYPE}:{row.id}:empty:{detail}"
    return record_event(
        db,
        _MANUAL_SOURCE_PAGE_TYPE,
        level="warning",
        media_id=media.id,
        item_id=item.id,
        payload=payload,
        dedupe_key=dedupe_key,
        when=moment,
    )


def record_manual_link_failed(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    *,
    reason: str,
    link: Link | None = None,
    now: datetime | None = None,
) -> Event:
    moment = _aware(now or datetime.now(timezone.utc))
    detail = scrub_url(reason.strip() or "erreur")[:300]
    log_manual_link_failed(media, item, link, reason=detail)
    message = f"Lien manuel en échec sans restriction ({detail})"
    payload: dict[str, Any] = {
        "download_id": row.id,
        "message": message,
        "reason": detail,
        "title": media.title,
        "season": item.season,
        "episode": item.episode,
        "issue": "error",
    }
    dedupe_key = f"{_MANUAL_LINK_ATTEMPT_TYPE}:{row.id}:fail:{detail}"
    return record_event(
        db,
        _MANUAL_LINK_ATTEMPT_TYPE,
        level="warning",
        media_id=media.id,
        item_id=item.id,
        payload=payload,
        dedupe_key=dedupe_key,
        when=moment,
    )


def record_download_source_attempt(
    db: Session,
    row: Download,
    media: Media,
    item: MediaItem,
    source: str,
    *,
    issue: str,
    reason: str | None = None,
    link_count: int | None = None,
    now: datetime | None = None,
) -> Event:
    """Un événement par (téléchargement, source). Mis à jour seulement si le motif change."""
    moment = _aware(now or datetime.now(timezone.utc))
    normalized = issue if issue in _ISSUES else "error"
    message = format_source_attempt_message(
        source,
        issue=normalized,
        reason=reason,
        link_count=link_count,
    )
    payload: dict[str, Any] = {
        "download_id": row.id,
        "source": source,
        "source_label": _source_label(source),
        "issue": normalized,
        "message": message,
        "title": media.title,
        "season": item.season,
        "episode": item.episode,
    }
    if reason:
        payload["reason"] = reason
    if link_count is not None:
        payload["link_count"] = int(link_count)
    dedupe_key = f"{_SOURCE_ATTEMPT_TYPE}:{row.id}:{source}"
    existing = db.scalar(select(Event).where(Event.type == _SOURCE_ATTEMPT_TYPE, Event.dedupe_key == dedupe_key))
    if existing is not None:
        if _fingerprint(existing.payload or {}) == _fingerprint(payload):
            return existing
        existing.payload = payload
        existing.level = "warning" if normalized in {"error", "refused"} else "info"
        existing.ts = moment
        existing.last_seen_at = moment
        db.flush()
        return existing
    return record_event(
        db,
        _SOURCE_ATTEMPT_TYPE,
        level="warning" if normalized in {"error", "refused"} else "info",
        media_id=media.id,
        item_id=item.id,
        payload=payload,
        dedupe_key=dedupe_key,
        when=moment,
    )

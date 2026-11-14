"""Journal dédoublonné par source pour chaque tentative de téléchargement."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.source_state import _source_label
from mediatracker.db.models import Download, Event, Media, MediaItem
from mediatracker.events import record_event

_SOURCE_ATTEMPT_TYPE = "download.source_attempt"
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

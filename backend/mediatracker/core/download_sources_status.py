"""Résumé des sources de téléchargement pour l'écran Réglages > Sources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.jobs import _text_setting
from mediatracker.core.selection import DEFAULT_DOWNLOAD_SOURCE_ORDER, parse_download_source_order
from mediatracker.core.source_state import DOWNLOAD_SOURCE_TYPES, _source_label, source_key
from mediatracker.db.models import SourceState


@dataclass(frozen=True)
class DownloadSourceStatus:
    source: str
    label: str
    enabled: bool
    available: bool
    rank: int
    state: str | None
    message: str
    since: datetime | None
    last_probe_at: datetime | None
    last_probe_link_count: int | None
    last_probe_duration_ms: int | None
    last_probe_issue: str | None
    last_probe_reason: str | None
    last_success_at: datetime | None


@dataclass(frozen=True)
class DownloadSourcesStatusBundle:
    sources: list[DownloadSourceStatus]
    available_sources: list[str]
    download_ready: bool
    blocked_message: str | None


def download_source_statuses(db: Session, settings: Settings) -> DownloadSourcesStatusBundle:
    from mediatracker.core.source_availability import DOWNLOAD_BLOCKED_MESSAGE, source_usable

    raw = _text_setting(db, "download_source_order", settings.download_source_order or DEFAULT_DOWNLOAD_SOURCE_ORDER)
    try:
        enabled = parse_download_source_order(raw)
    except ValueError:
        enabled = parse_download_source_order(DEFAULT_DOWNLOAD_SOURCE_ORDER)
    enabled_set = set(enabled)
    tail = [name for name in DOWNLOAD_SOURCE_TYPES if name not in enabled_set]
    order = list(enabled) + tail
    rank_by_source = {name: index + 1 for index, name in enumerate(enabled)}
    out: list[DownloadSourceStatus] = []
    available_sources: list[str] = []
    for name in order:
        usable = source_usable(db, settings, name)
        if usable:
            available_sources.append(name)
        row = db.get(SourceState, source_key(name))
        out.append(
            DownloadSourceStatus(
                source=name,
                label=_source_label(name),
                enabled=name in enabled_set,
                available=usable,
                rank=rank_by_source.get(name, 0),
                state=row.state if row is not None else None,
                message=(row.message or "") if row is not None else "",
                since=row.since if row is not None else None,
                last_probe_at=row.last_probe_at if row is not None else None,
                last_probe_link_count=row.last_probe_link_count if row is not None else None,
                last_probe_duration_ms=row.last_probe_duration_ms if row is not None else None,
                last_probe_issue=row.last_probe_issue if row is not None else None,
                last_probe_reason=row.last_probe_reason if row is not None else None,
                last_success_at=row.last_success_at if row is not None else None,
            )
        )
    ready = any(source_usable(db, settings, name) for name in enabled)
    return DownloadSourcesStatusBundle(
        sources=out,
        available_sources=available_sources,
        download_ready=ready,
        blocked_message=None if ready else DOWNLOAD_BLOCKED_MESSAGE,
    )

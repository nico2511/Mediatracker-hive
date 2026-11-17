"""Historique d'état des sources Wawacity/Comet et hébergeurs AllDebrid."""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from mediatracker.db.models import Download, Link, SourceState, SourceStateChange
from mediatracker.events import record_event
from mediatracker.retention import SOURCE_STATE_DAYS as RETENTION_DAYS
from mediatracker.security.logging import redact

logger = logging.getLogger(__name__)
TRACKED_SOURCES = frozenset({"wawacity", "free_telecharger", "comet", "torrentio", "stremio_addons"})
DOWNLOAD_SOURCE_TYPES = frozenset(
    {"wawacity", "free_telecharger", "hive", "comet", "torrentio", "stremio_addons"}
)
STATE_OK = "ok"
STATE_DOWN = "down"

_LOCK = threading.Lock()
_last_signal: dict[str, bool] = {}
_pending: dict[str, _PendingObservation] = {}

# AllDebrid API keys are long alphanumeric strings without a prefix in some error payloads.
_BARE_ALLDEBRID_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{32,})(?![A-Za-z0-9])")
_UUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_PURE_HEX_DIGEST = re.compile(r"^[0-9a-f]{32}$|^[0-9a-f]{40}$|^[0-9a-f]{64}$", re.IGNORECASE)
_PUBLIC_MESSAGE_LIMIT = 500


@dataclass(frozen=True)
class _PendingObservation:
    kind: str
    label: str
    up: bool
    message: str


def source_key(type_name: str) -> str:
    return f"source:{type_name}"


def _is_diagnostic_digest(token: str) -> bool:
    if _UUID.fullmatch(token):
        return True
    return _PURE_HEX_DIGEST.fullmatch(token) is not None


def _redact_bare_alldebrid_key(message: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        if _is_diagnostic_digest(token):
            return token
        return "[redacted]"

    return _BARE_ALLDEBRID_KEY.sub(replace, message)


def public_source_message(message: str) -> str:
    """Strip secrets before persisting or returning a source health / probe message."""
    cleaned = _redact_bare_alldebrid_key(redact((message or "").strip()))
    return cleaned[:_PUBLIC_MESSAGE_LIMIT] or ""


def hoster_key(name: str) -> str:
    return f"hoster:{name.casefold()}"


def note_tracked_source_signal(type_name: str, *, up: bool, message: str = "") -> None:
    """Mémorise le dernier résultat d'une requête source (compteurs H2). Pas d'écriture SQLite."""
    if type_name not in TRACKED_SOURCES:
        return
    key = source_key(type_name)
    with _LOCK:
        if _last_signal.get(key) == up:
            return
        _last_signal[key] = up
        _pending[key] = _PendingObservation("source", _source_label(type_name), up, message)


def note_hoster_signal(hoster: str | None, *, up: bool, message: str = "") -> None:
    name = (hoster or "").strip()
    if not name:
        return
    key = hoster_key(name)
    with _LOCK:
        if _last_signal.get(key) == up:
            return
        _last_signal[key] = up
        _pending[key] = _PendingObservation("hoster", name, up, message)


def flush_pending_source_observations(db: Session, *, when: datetime | None = None) -> int:
    """Écrit en base uniquement les transitions en attente."""
    with _LOCK:
        batch = dict(_pending)
        _pending.clear()
    written = 0
    for key, obs in batch.items():
        if _apply(db, key=key, kind=obs.kind, label=obs.label, up=obs.up, message=obs.message, when=when):
            written += 1
    return written


PROBE_EMPTY_MESSAGE = "Aucun résultat pour ce titre"


def record_source_probe(
    db: Session,
    *,
    type_name: str,
    issue: str,
    link_count: int,
    duration_ms: int,
    reason: str | None = None,
    when: datetime | None = None,
) -> None:
    """Mémorise le dernier test manuel (réglages Sources)."""
    if type_name not in DOWNLOAD_SOURCE_TYPES:
        return
    moment = _aware(when or datetime.now(timezone.utc))
    key = source_key(type_name)
    label = _source_label(type_name)
    row = db.get(SourceState, key)
    if row is None:
        row = SourceState(
            target_key=key,
            kind="source",
            label=label,
            state=STATE_OK,
            message=None,
            since=moment,
            updated_at=moment,
        )
        db.add(row)
    row.last_probe_at = moment
    row.last_probe_link_count = link_count
    row.last_probe_duration_ms = duration_ms
    row.last_probe_issue = (issue or "")[:16] or None
    row.last_probe_reason = public_source_message(reason or "") or None
    row.updated_at = moment
    if issue == "found" or (issue == "empty" and type_name == "hive"):
        row.last_success_at = moment
    db.flush()


def note_probe_source_health(
    db: Session,
    *,
    type_name: str,
    issue: str,
    reason: str | None,
    when: datetime | None = None,
) -> None:
    """Met à jour l'état en ligne / panne à partir d'un test manuel."""
    if type_name not in TRACKED_SOURCES:
        return
    message = public_source_message(reason or "")
    if issue == "found":
        observe_source_outcome(db, type_name=type_name, up=True, message=message, when=when)
        return
    if issue == "empty":
        empty_message = message or PROBE_EMPTY_MESSAGE
        observe_source_outcome(db, type_name=type_name, up=True, message=empty_message, when=when)
        return
    if issue == "error":
        err_msg = message or "Erreur de test"
        observe_source_outcome(
            db,
            type_name=type_name,
            up=False,
            message=err_msg,
            when=when,
        )
        key = source_key(type_name)
        row = db.get(SourceState, key)
        if row is not None and row.state == STATE_DOWN:
            moment = _aware(when or datetime.now(timezone.utc))
            cleaned = public_source_message(err_msg)
            row.message = cleaned[:500] or None
            row.updated_at = moment
            db.flush()


def observe_source_outcome(
    db: Session,
    *,
    type_name: str,
    up: bool,
    message: str = "",
    when: datetime | None = None,
) -> None:
    """API directe pour les tests."""
    if type_name not in DOWNLOAD_SOURCE_TYPES:
        return
    _apply(
        db,
        key=source_key(type_name),
        kind="source",
        label=_source_label(type_name),
        up=up,
        message=message,
        when=when,
    )


def observe_hoster_outcome(
    db: Session,
    *,
    hoster: str | None,
    up: bool,
    message: str = "",
    when: datetime | None = None,
) -> None:
    """API directe pour les tests."""
    name = (hoster or "").strip()
    if not name:
        return
    _apply(db, key=hoster_key(name), kind="hoster", label=name, up=up, message=message, when=when)


def reset_source_state_memory() -> None:
    with _LOCK:
        _last_signal.clear()
        _pending.clear()


def current_states(db: Session) -> list[SourceState]:
    return list(db.scalars(select(SourceState).order_by(SourceState.kind, SourceState.label)).all())


def last_successful_debrid_by_hoster(db: Session) -> dict[str, datetime]:
    """Dernier débridage AllDebrid réussi par hébergeur (clé casefold), sans appel réseau."""
    rows = db.execute(
        select(Link.hoster, func.max(Download.attempt_started_at))
        .join(Link, Download.link_id == Link.id)
        .where(Download.debrid_link.is_not(None), Link.hoster.is_not(None))
        .group_by(Link.hoster)
    ).all()
    out: dict[str, datetime] = {}
    for hoster, moment in rows:
        name = (hoster or "").strip()
        if not name or moment is None:
            continue
        out[name.casefold()] = _aware(moment)
    return out


def recent_changes(db: Session, *, limit: int = 50) -> list[SourceStateChange]:
    query = (
        select(SourceStateChange)
        .order_by(SourceStateChange.changed_at.desc(), SourceStateChange.id.desc())
        .limit(limit)
    )
    return list(db.scalars(query).all())


def maybe_purge_old_changes(db: Session, *, now: datetime | None = None, batch_size: int = 500) -> int:
    moment = _aware(now or datetime.now(timezone.utc))
    cutoff = moment - timedelta(days=RETENTION_DAYS)
    removed = 0
    while True:
        ids = list(
            db.scalars(
                select(SourceStateChange.id)
                .where(SourceStateChange.changed_at < cutoff)
                .order_by(SourceStateChange.id)
                .limit(batch_size)
            ).all()
        )
        if not ids:
            break
        db.execute(delete(SourceStateChange).where(SourceStateChange.id.in_(ids)))
        removed += len(ids)
        if len(ids) < batch_size:
            break
    if removed:
        logger.info("États sources : %s changement(s) de plus de %s jours supprimé(s).", removed, RETENTION_DAYS)
    return removed


def _apply(
    db: Session,
    *,
    key: str,
    kind: str,
    label: str,
    up: bool,
    message: str,
    when: datetime | None,
) -> bool:
    moment = _aware(when or datetime.now(timezone.utc))
    new_state = STATE_OK if up else STATE_DOWN
    message = public_source_message(message)
    row = db.get(SourceState, key)
    if row is not None and row.state == new_state:
        return False
    previous = row.state if row is not None else None
    if row is None:
        row = SourceState(
            target_key=key,
            kind=kind,
            label=label,
            state=new_state,
            message=message[:500] or None,
            since=moment,
            updated_at=moment,
        )
        db.add(row)
    else:
        row.state = new_state
        row.message = message[:500] or None
        row.since = moment
        row.updated_at = moment
    db.add(
        SourceStateChange(
            target_key=key,
            kind=kind,
            label=label,
            state=new_state,
            message=message[:500] or None,
            changed_at=moment,
        )
    )
    _journal_transition(db, key=key, label=label, previous=previous, new_state=new_state, message=message, when=moment)
    with _LOCK:
        _last_signal[key] = up
    return True


def _journal_transition(
    db: Session,
    *,
    key: str,
    label: str,
    previous: str | None,
    new_state: str,
    message: str,
    when: datetime,
) -> None:
    if new_state == STATE_DOWN:
        record_event(
            db,
            "source.outage",
            level="warning",
            dedupe_key=f"source.outage:{key}:{int(when.timestamp())}",
            when=when,
            payload={
                "target_key": key,
                "title": label,
                "message": message or f"{label} est en panne.",
                "level": "warning",
            },
        )
        return
    if new_state == STATE_OK and previous == STATE_DOWN:
        record_event(
            db,
            "source.recovered",
            level="info",
            dedupe_key=f"source.recovered:{key}:{int(when.timestamp())}",
            when=when,
            payload={
                "target_key": key,
                "title": label,
                "message": message or f"{label} répond à nouveau.",
                "level": "info",
            },
        )


def _source_label(type_name: str) -> str:
    if type_name == "wawacity":
        return "Wawacity"
    if type_name == "comet":
        return "Comet"
    if type_name == "torrentio":
        return "Torrentio"
    if type_name == "stremio_addons":
        return "Addons Stremio"
    if type_name == "free_telecharger":
        return "Free-télécharger"
    if type_name == "hive":
        return "Hive"
    return type_name


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

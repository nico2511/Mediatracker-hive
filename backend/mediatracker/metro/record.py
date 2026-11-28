"""Écriture task_run : tampon en passe, flush après commit worker."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import delete, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from mediatracker.db.models import TaskRun
from mediatracker.db.session import is_sqlite_locked
from mediatracker.metro.cause import scrub_cause, scrub_exception_cause
from mediatracker.metro.collection import is_collection_active
from mediatracker.metro.graph import edge_between, station_line

logger = logging.getLogger(__name__)

_monotonic = time.monotonic


def _mono_now() -> float:
    return _monotonic()


TriggerKind = Literal["schedule", "event", "user", "peer"]
OutcomeKind = Literal["ok", "nothing", "blocked", "deferred", "error"]

FLUSH_BUSY_MS = 200
MAX_BUFFER_ROWS = 5000
HEARTBEAT_MIN_INTERVAL_S = 60.0
PURGE_MIN_INTERVAL_S = 3600.0
SOC1_SLOW_PASS_MS = 30_000
STATION_RUN_MIN_MS = 500
RETENTION_DAYS = 7

_pass_collection: threading.local = threading.local()
_current_run_local: threading.local = threading.local()
_pass_factory: threading.local = threading.local()
_pass_saw_locked: threading.local = threading.local()

_LOCK = threading.Lock()
_local_id_seq = 0
_buffer: list[dict[str, Any]] = []
_dropped_buffer_rows = 0
_open_blocked: dict[tuple[str, str], _BlockedState] = {}
_open_adb2_waits: dict[int, _BlockedState] = {}
_pass_idle_state: dict[str, str] = {}
_pass_child_count = 0
_last_purge_monotonic = 0.0
_deferred_src8: list[Any] = []
_warn_last: dict[str, float] = {}
_local_id_to_db: dict[int, int] = {}
_dropped_flush_batches = 0
_flush_busy_wait_ms = 0.0

_download_sequences: dict[int, str] = {}
_sequence_station_locals: dict[str, dict[str, int]] = {}
_hour_pass_stats: dict[str, dict[str, Any]] = {}
_pass_hour_key: threading.local = threading.local()
_adb3_unlock_ms: threading.local = threading.local()


@dataclass
class _BlockedState:
    run_local_id: int
    last_heartbeat_mono: float
    db_id: int | None = None


@dataclass
class _ActiveRun:
    run_id: int | None
    station: str
    started_monotonic: float = field(default_factory=_mono_now)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _log_throttled(key: str, msg: str, *, exc_info: bool = False) -> None:
    now = time.monotonic()
    last = _warn_last.get(key, 0.0)
    if now - last < 60.0:
        return
    _warn_last[key] = now
    logger.warning(msg, exc_info=exc_info)


def is_pass_collection_active() -> bool:
    return bool(getattr(_pass_collection, "value", False))


def load_pass_collection_flag(factory: sessionmaker[Session]) -> None:
    session = factory()
    try:
        active = is_collection_active(session)
    except Exception:
        logger.debug("Lecture interrupteur task_run impossible.", exc_info=True)
        active = False
    finally:
        session.close()
    prev = is_pass_collection_active()
    if prev and not active:
        hour_key = getattr(_pass_hour_key, "value", None)
        if hour_key:
            try:
                _rollup_hour_pass(hour_key, allow_when_inactive=True)
            except Exception:
                _log_throttled("rollup_stop", "Rollup horaire à l'arrêt collecte ignoré.", exc_info=True)
            _pass_hour_key.value = None
        with _LOCK:
            _open_blocked.clear()
            _open_adb2_waits.clear()
            _pass_idle_state.clear()
    _pass_collection.value = active


def set_pass_factory(factory: sessionmaker[Session] | None) -> None:
    _pass_factory.value = factory


def get_pass_factory() -> sessionmaker[Session] | None:
    return getattr(_pass_factory, "value", None)


def sequence_for_download(download_id: int) -> str:
    seq = _download_sequences.get(download_id)
    if seq is None:
        seq = f"dl-{download_id}"
        _download_sequences[download_id] = seq
    return seq


def release_download_sequence(download_id: int) -> None:
    seq = _download_sequences.pop(download_id, None)
    if seq:
        with _LOCK:
            _sequence_station_locals.pop(seq, None)


def clear_adb3_unlock_ms() -> None:
    _adb3_unlock_ms.value = 0


def note_adb3_unlock_ms(ms: int) -> None:
    if ms <= 0:
        return
    prev = int(getattr(_adb3_unlock_ms, "value", 0) or 0)
    _adb3_unlock_ms.value = prev + ms


def take_adb3_unlock_ms() -> int | None:
    total = int(getattr(_adb3_unlock_ms, "value", 0) or 0)
    clear_adb3_unlock_ms()
    return total if total > 0 else None


def account_hourly_station(station: str, duration_ms: int) -> None:
    try:
        if duration_ms <= 0 or not is_pass_collection_active():
            return
        hour_key = _hour_bucket_key(_utcnow())
        bucket = _hour_pass_stats.setdefault(
            hour_key,
            {"passes": 0, "empty": 0, "durations": [], "stations": {}},
        )
        stations: dict[str, list[int]] = bucket.setdefault("stations", {})
        stations.setdefault(station, []).append(duration_ms)
    except Exception:
        _log_throttled("hourly_station", "Agrégat horaire station ignoré.", exc_info=True)


def media_subject(media_id: int) -> str:
    return f"media:{media_id}"


def _media_id_from_subject(subject_ref: str | None) -> int | None:
    if subject_ref and subject_ref.startswith("media:"):
        try:
            return int(subject_ref.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
    return None


def note_sequence_station(sequence_id: str | None, station: str, local_id: int | None) -> None:
    if sequence_id and local_id is not None:
        with _LOCK:
            _sequence_station_locals.setdefault(sequence_id, {})[station] = local_id


def parent_local_for_trigger(sequence_id: str | None, origin_station: str) -> int | None:
    if not sequence_id:
        return None
    with _LOCK:
        return _sequence_station_locals.get(sequence_id, {}).get(origin_station)


def bind_download_sequence(download_id: int, row: Any = None) -> None:
    try:
        if row is not None:
            existing = getattr(row, "metro_sequence_id", None)
            if existing:
                text = str(existing).strip()
                if text:
                    _download_sequences[download_id] = text
                    return
            created = getattr(row, "created_at", None)
            if created is not None:
                try:
                    stamp = int(created.timestamp())
                except (TypeError, ValueError, OSError):
                    stamp = 0
                seq = f"dl-{download_id}-{stamp}"
                _download_sequences[download_id] = seq
                if is_pass_collection_active() and hasattr(row, "metro_sequence_id"):
                    row.metro_sequence_id = seq
                return
        sequence_for_download(download_id)
    except Exception:
        _log_throttled("bind_sequence", "Liaison séquence download ignorée.", exc_info=True)


def get_sequence_id(download_id: int | None = None) -> str | None:
    if download_id is not None:
        return _download_sequences.get(download_id)
    return getattr(_current_run_local, "sequence", None)


def _next_local_id() -> int:
    global _local_id_seq
    _local_id_seq -= 1
    return _local_id_seq


def _get_current_run_local() -> int | None:
    return getattr(_current_run_local, "run_id", None)


def _set_current_run_local(run_id: int | None) -> int | None:
    previous = _get_current_run_local()
    _current_run_local.run_id = run_id
    return previous


def reset_pass_sqlite_lock_flag() -> None:
    _pass_saw_locked.value = False


def pass_saw_sqlite_locked() -> bool:
    return bool(getattr(_pass_saw_locked, "value", False))


def mark_pass_sqlite_locked() -> None:
    _pass_saw_locked.value = True


def reset_metro_instrumentation_state() -> None:
    global _local_id_seq, _pass_child_count, _dropped_buffer_rows
    reset_pass_sqlite_lock_flag()
    _pass_collection.value = False
    _set_current_run_local(None)
    _pass_child_count = 0
    _local_id_seq = 0
    with _LOCK:
        _buffer.clear()
        _open_blocked.clear()
        _open_adb2_waits.clear()
        _pass_idle_state.clear()
        _local_id_to_db.clear()
        _deferred_src8.clear()
        _download_sequences.clear()
        _sequence_station_locals.clear()
        _hour_pass_stats.clear()
    reset_flush_metrics()
    _dropped_buffer_rows = 0


def _reset_pass_state() -> None:
    global _pass_child_count
    _pass_child_count = 0
    reset_pass_sqlite_lock_flag()
    _set_current_run_local(None)


def _coerce_subject(subject_ref: str | None) -> str | None:
    if subject_ref is None:
        return None
    text_val = subject_ref.strip()
    if not text_val:
        return None
    return text_val[:64]


def _subject_download_id(subject_ref: str | None) -> int | None:
    if not subject_ref or not subject_ref.startswith("download:"):
        return None
    try:
        return int(subject_ref.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def _resolve_sequence(subject_ref: str | None, sequence_id: str | None) -> str | None:
    if sequence_id is not None:
        return sequence_id
    download_id = _subject_download_id(subject_ref)
    if download_id is not None:
        return sequence_for_download(download_id)
    return get_sequence_id()


def _append_buffer(payload: dict[str, Any], *, count_child: bool = True) -> int | None:
    global _dropped_buffer_rows, _pass_child_count
    if not is_pass_collection_active():
        return None
    local_id = payload.get("local_id") or _next_local_id()
    payload["local_id"] = local_id
    with _LOCK:
        if len(_buffer) >= MAX_BUFFER_ROWS:
            _dropped_buffer_rows += 1
            _buffer.pop(0)
        _buffer.append(payload)
    if count_child and payload.get("station") != "SOC1":
        _pass_child_count += 1
    return local_id


def _prepend_buffer(payload: dict[str, Any]) -> int | None:
    global _dropped_buffer_rows
    if not is_pass_collection_active():
        return None
    local_id = payload.get("local_id") or _next_local_id()
    payload["local_id"] = local_id
    with _LOCK:
        if len(_buffer) >= MAX_BUFFER_ROWS:
            _dropped_buffer_rows += 1
            _buffer.pop(0)
        _buffer.insert(0, payload)
    return local_id


def _append_update(*, local_id: int | None = None, db_id: int | None = None, **fields: Any) -> None:
    if not is_pass_collection_active():
        return
    with _LOCK:
        _buffer.append({"op": "update", "local_id": local_id, "db_id": db_id, "fields": fields})


def record_task_run(
    *,
    station: str,
    trigger: TriggerKind,
    outcome: OutcomeKind,
    parent_run_id: int | None = None,
    parent_local_id: int | None = None,
    edge_id: str | None = None,
    cause: str | None = None,
    subject_ref: str | None = None,
    blocked_by: str | None = None,
    waiting_on: str | None = None,
    sequence_id: str | None = None,
    media_id: int | None = None,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    duration_ms: int | None = None,
) -> int | None:
    try:
        if not is_pass_collection_active():
            return None
        moment = started_at or _utcnow()
        parent_local = parent_local_id
        if parent_run_id is None and parent_local is None:
            parent_local = _get_current_run_local()
        resolved_media = media_id if media_id is not None else _media_id_from_subject(subject_ref)
        payload = {
            "op": "insert",
            "line": station_line(station),
            "station": station,
            "sequence_id": _resolve_sequence(subject_ref, sequence_id),
            "parent_run_id": parent_run_id,
            "parent_local_id": parent_local,
            "edge_id": edge_id,
            "trigger": trigger,
            "cause": scrub_cause(cause),
            "subject_ref": _coerce_subject(subject_ref),
            "media_id": resolved_media,
            "blocked_by": blocked_by,
            "waiting_on": waiting_on,
            "outcome": outcome,
            "started_at": moment,
            "finished_at": finished_at if finished_at is not None else moment,
            "heartbeat_at": moment,
            "duration_ms": duration_ms,
        }
        return _append_buffer(payload)
    except Exception:
        _log_throttled("record_task_run", "record_task_run ignoré.", exc_info=True)
        return None


def clear_blocked(station: str, blocked_by: str) -> None:
    try:
        if not is_pass_collection_active():
            return
        key = (station, blocked_by)
        with _LOCK:
            state = _open_blocked.pop(key, None)
        if state is None:
            return
        end = _utcnow()
        db_id = state.db_id or _local_id_to_db.get(state.run_local_id)
        _append_update(
            local_id=state.run_local_id,
            db_id=db_id,
            finished_at=end,
            heartbeat_at=end,
        )
    except Exception:
        _log_throttled("clear_blocked", "clear_blocked ignoré.", exc_info=True)


def record_blocked_continuous(
    *,
    station: str,
    blocked_by: str,
    trigger: TriggerKind = "schedule",
    cause: str | None = None,
    edge_id: str | None = None,
) -> None:
    try:
        if not is_pass_collection_active():
            return
        key = (station, blocked_by)
        now_mono = _monotonic()
        with _LOCK:
            state = _open_blocked.get(key)
        if state is not None:
            if now_mono - state.last_heartbeat_mono < HEARTBEAT_MIN_INTERVAL_S:
                return
            state.last_heartbeat_mono = now_mono
            db_id = state.db_id or _local_id_to_db.get(state.run_local_id)
            if db_id is not None:
                state.db_id = db_id
            _append_update(local_id=state.run_local_id, db_id=db_id, heartbeat_at=_utcnow())
            return
        edge = edge_id or edge_between(_infer_block_origin(blocked_by), station, "bloque")
        local_id = record_task_run(
            station=station,
            trigger=trigger,
            outcome="blocked",
            blocked_by=blocked_by,
            cause=cause,
            edge_id=edge,
            finished_at=None,
        )
        if local_id is not None:
            with _LOCK:
                _open_blocked[key] = _BlockedState(run_local_id=local_id, last_heartbeat_mono=now_mono)
    except Exception:
        _log_throttled("record_blocked_continuous", "record_blocked_continuous ignoré.", exc_info=True)


def touch_adb2_magnet_wait(
    *,
    download_id: int,
    media_id: int | None = None,
    sequence_id: str | None = None,
) -> None:
    """One ADB2 task_run line per magnet wait (outcome deferred), heartbeats only on poll."""
    try:
        if not is_pass_collection_active():
            return
        now_mono = _monotonic()
        with _LOCK:
            state = _open_adb2_waits.get(download_id)
        if state is not None:
            if now_mono - state.last_heartbeat_mono < HEARTBEAT_MIN_INTERVAL_S:
                return
            state.last_heartbeat_mono = now_mono
            db_id = state.db_id or _local_id_to_db.get(state.run_local_id)
            if db_id is not None:
                state.db_id = db_id
            _append_update(local_id=state.run_local_id, db_id=db_id, heartbeat_at=_utcnow())
            return
        subject = f"download:{download_id}"
        local_id = record_task_run(
            station="ADB2",
            trigger="event",
            outcome="deferred",
            subject_ref=subject,
            media_id=media_id,
            sequence_id=sequence_id or sequence_for_download(download_id),
            edge_id=edge_between("DL1", "ADB2", "attend"),
            finished_at=None,
        )
        if local_id is not None:
            with _LOCK:
                _open_adb2_waits[download_id] = _BlockedState(run_local_id=local_id, last_heartbeat_mono=now_mono)
    except Exception:
        _log_throttled("touch_adb2_magnet_wait", "touch_adb2_magnet_wait ignoré.", exc_info=True)


def close_adb2_magnet_wait(*, download_id: int, outcome: Literal["ok", "error"]) -> None:
    try:
        if not is_pass_collection_active():
            return
        with _LOCK:
            state = _open_adb2_waits.pop(download_id, None)
        if state is None:
            return
        end = _utcnow()
        db_id = state.db_id or _local_id_to_db.get(state.run_local_id)
        _append_update(
            local_id=state.run_local_id,
            db_id=db_id,
            finished_at=end,
            heartbeat_at=end,
            outcome=outcome,
        )
    except Exception:
        _log_throttled("close_adb2_magnet_wait", "close_adb2_magnet_wait ignoré.", exc_info=True)


def _infer_block_origin(blocked_by: str) -> str:
    if blocked_by.startswith("ADB1"):
        return "ADB1"
    if blocked_by.startswith("DL8"):
        return "DL8"
    if blocked_by.startswith("DL9"):
        return "DL9"
    if blocked_by.startswith("BIB3"):
        return "BIB3"
    if blocked_by.startswith("SOC7"):
        return "SOC7"
    if blocked_by.startswith("SOC3"):
        return "SOC3"
    return "SOC7"


def record_pass_idle_change(
    *,
    station: str,
    state_key: str,
    outcome: OutcomeKind,
    dedupe_same_state: bool = False,
    **kwargs: Any,
) -> None:
    try:
        if not is_pass_collection_active():
            return
        with _LOCK:
            previous = _pass_idle_state.get(station)
            if dedupe_same_state and previous == state_key:
                return
            _pass_idle_state[station] = state_key
        if outcome == "nothing":
            if previous != state_key:
                clear_pass_state_side_effect(station, previous, state_key)
            return
        record_task_run(station=station, trigger=kwargs.pop("trigger", "schedule"), outcome=outcome, **kwargs)
        if previous != state_key:
            clear_pass_state_side_effect(station, previous, state_key)
    except Exception:
        _log_throttled("record_pass_idle_change", "record_pass_idle_change ignoré.", exc_info=True)


def clear_pass_state_side_effect(station: str, previous: str | None, new: str) -> None:
    if station == "JF4" and previous == "scanning" and new != "scanning":
        record_task_run(station="JF4", trigger="schedule", outcome="ok", cause="Scan Jellyfin terminé")


def defer_source_apply_download(row: Any) -> None:
    if not is_pass_collection_active():
        return
    with _LOCK:
        _deferred_src8.append(row)


def resolve_deferred_instruments() -> None:
    try:
        rows = []
        with _LOCK:
            rows = list(_deferred_src8)
            _deferred_src8.clear()
        factory = get_pass_factory()
        for row in rows:
            download_id = getattr(row, "id", None)
            if download_id is None:
                continue
            from mediatracker.metro.instrument import instrument_source_apply_link

            media_id = None
            item_id = getattr(row, "media_item_id", None)
            if item_id is not None and factory is not None:
                try:
                    with factory() as probe:
                        from mediatracker.db.models import MediaItem

                        item = probe.get(MediaItem, item_id)
                        if item is not None:
                            media_id = item.media_id
                except Exception:
                    pass
            instrument_source_apply_link(download_id, media_id=media_id, row=row)
    except Exception:
        _log_throttled("resolve_deferred", "resolve_deferred_instruments ignoré.", exc_info=True)


def _flush_session(factory: sessionmaker[Session]) -> Session:
    session = factory()
    try:
        bind = session.get_bind()
        if bind.dialect.name == "sqlite":
            conn = session.connection()
            conn.execute(text(f"PRAGMA busy_timeout={FLUSH_BUSY_MS}"))
    except Exception:
        pass
    return session


def flush_pass_buffer(factory: sessionmaker[Session]) -> int:
    global _dropped_buffer_rows, _dropped_flush_batches, _flush_busy_wait_ms

    try:
        with _LOCK:
            if not _buffer:
                return 0
            batch = list(_buffer)
            _buffer.clear()
        session = _flush_session(factory)
        try:
            id_map: dict[int, int] = {}
            written = 0
            inserts = [row for row in batch if row.get("op", "insert") == "insert"]
            updates = [row for row in batch if row.get("op") == "update"]
            for row in inserts:
                parent_db = row.get("parent_run_id")
                parent_local = row.get("parent_local_id")
                if parent_db is None and parent_local is not None:
                    parent_db = id_map.get(parent_local) or _local_id_to_db.get(parent_local)
                db_row = TaskRun(
                    line=row["line"],
                    station=row["station"],
                    sequence_id=row.get("sequence_id"),
                    parent_run_id=parent_db,
                    edge_id=row.get("edge_id"),
                    trigger=row["trigger"],
                    cause=row.get("cause"),
                    subject_ref=row.get("subject_ref"),
                    media_id=row.get("media_id"),
                    blocked_by=row.get("blocked_by"),
                    waiting_on=row.get("waiting_on"),
                    outcome=row["outcome"],
                    started_at=row["started_at"],
                    finished_at=row.get("finished_at"),
                    heartbeat_at=row.get("heartbeat_at"),
                    duration_ms=row.get("duration_ms"),
                )
                session.add(db_row)
                session.flush()
                local_id = row["local_id"]
                id_map[local_id] = db_row.id
                written += 1
                with _LOCK:
                    for state in _open_blocked.values():
                        if state.run_local_id == local_id:
                            state.db_id = db_row.id
                    for state in _open_adb2_waits.values():
                        if state.run_local_id == local_id:
                            state.db_id = db_row.id
                    _local_id_to_db[local_id] = db_row.id
            for row in updates:
                target_id = row.get("db_id")
                local_id = row.get("local_id")
                if target_id is None and local_id is not None:
                    target_id = id_map.get(local_id) or _local_id_to_db.get(local_id)
                if target_id is None:
                    continue
                session.execute(update(TaskRun).where(TaskRun.id == target_id).values(**row["fields"]))
            session.commit()
            with _LOCK:
                keep_local = {state.run_local_id for state in _open_blocked.values()}
                keep_local |= {state.run_local_id for state in _open_adb2_waits.values()}
                keep_local |= {
                    row.get("local_id")
                    for row in updates
                    if row.get("local_id") is not None
                }
                for local_id in list(_local_id_to_db):
                    if local_id not in keep_local:
                        del _local_id_to_db[local_id]
            return written
        except OperationalError as exc:
            session.rollback()
            if is_sqlite_locked(exc):
                _flush_busy_wait_ms += FLUSH_BUSY_MS
                with _LOCK:
                    merged = batch + list(_buffer)
                    overflow = max(0, len(merged) - MAX_BUFFER_ROWS)
                    if overflow:
                        _dropped_buffer_rows += overflow
                        merged = merged[-MAX_BUFFER_ROWS:]
                    _buffer.clear()
                    _buffer.extend(merged)
            else:
                _dropped_flush_batches += 1
                _log_throttled(
                    "flush_operational",
                    f"Flush task_run abandonné ({len(batch)} lignes) : {type(exc).__name__}",
                )
            return 0
        except Exception:
            session.rollback()
            _dropped_flush_batches += 1
            _log_throttled("flush", f"Flush task_run abandonné ({len(batch)} lignes).", exc_info=True)
            return 0
        finally:
            session.close()
    except Exception:
        _log_throttled("flush_outer", "flush_pass_buffer ignoré.", exc_info=True)
        return 0


@contextmanager
def station_run(
    station: str,
    trigger: TriggerKind,
    *,
    subject_ref: str | None = None,
    waiting_on: str | None = None,
    parent_edge_from: str | None = None,
    media_id: int | None = None,
    outcome: OutcomeKind = "ok",
) -> Iterator[_ActiveRun]:
    token_prev = _get_current_run_local()
    active = _ActiveRun(run_id=None, station=station)
    if not is_pass_collection_active():
        yield active
        return
    started_mono = _monotonic()
    try:
        yield active
    except Exception as exc:
        duration_ms = int((_monotonic() - started_mono) * 1000)
        if duration_ms >= STATION_RUN_MIN_MS:
            edge_id = edge_between(parent_edge_from, station) if parent_edge_from else None
            record_task_run(
                station=station,
                trigger=trigger,
                outcome="error",
                subject_ref=subject_ref,
                waiting_on=waiting_on,
                edge_id=edge_id,
                media_id=media_id,
                cause=scrub_exception_cause(exc),
                finished_at=_utcnow(),
                duration_ms=duration_ms,
            )
        raise
    else:
        duration_ms = int((_monotonic() - started_mono) * 1000)
        if duration_ms >= STATION_RUN_MIN_MS:
            edge_id = edge_between(parent_edge_from, station) if parent_edge_from else None
            local_id = record_task_run(
                station=station,
                trigger=trigger,
                outcome=outcome,
                subject_ref=subject_ref,
                waiting_on=waiting_on,
                edge_id=edge_id,
                media_id=media_id,
                finished_at=_utcnow(),
                duration_ms=duration_ms,
            )
            active.run_id = local_id
    finally:
        _set_current_run_local(token_prev)


def _hour_bucket_key(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def _rollup_hour_pass(hour_key: str, *, allow_when_inactive: bool = False) -> None:
    stats = _hour_pass_stats.pop(hour_key, None)
    if not stats:
        return
    durations: list[int] = stats.get("durations", [])
    if not durations:
        return
    ordered = sorted(durations)
    median_ms = ordered[len(ordered) // 2]
    max_ms = ordered[-1]
    passes = int(stats.get("passes", 0))
    empty = int(stats.get("empty", 0))
    extra: list[str] = []
    stations: dict[str, list[int]] = stats.get("stations") or {}
    for station in sorted(stations):
        values = stations.get(station) or []
        if not values:
            continue
        sorted_vals = sorted(values)
        extra.append(
            f"{station}:n={len(values)} med={sorted_vals[len(sorted_vals) // 2]} max={sorted_vals[-1]}"
        )
    cause = f"heure={hour_key} passes={passes} vides={empty} med_ms={median_ms} max_ms={max_ms}"
    if extra:
        cause = f"{cause} " + " ".join(extra)
    if not is_pass_collection_active() and not allow_when_inactive:
        return
    record_task_run(
        station="SOC1",
        trigger="schedule",
        outcome="ok",
        cause=cause,
        duration_ms=median_ms,
    )


def _account_hourly_pass(duration_ms: int, empty: bool, moment: datetime) -> None:
    try:
        hour_key = _hour_bucket_key(moment)
        prev_key = getattr(_pass_hour_key, "value", None)
        if prev_key is not None and prev_key != hour_key:
            _rollup_hour_pass(prev_key)
        _pass_hour_key.value = hour_key
        bucket = _hour_pass_stats.setdefault(
            hour_key,
            {"passes": 0, "empty": 0, "durations": [], "stations": {}},
        )
        bucket["passes"] += 1
        if empty:
            bucket["empty"] += 1
        bucket["durations"].append(duration_ms)
    except Exception:
        _log_throttled("hourly_pass", "Agrégat horaire SOC1 ignoré.", exc_info=True)


@contextmanager
def metro_pass() -> Iterator[None]:
    started = _monotonic()
    started_at = _utcnow()
    _reset_pass_state()
    soc1_local: int | None = None
    try:
        if is_pass_collection_active():
            soc1_local = _next_local_id()
            _set_current_run_local(soc1_local)
        yield
    finally:
        try:
            duration_ms = int((_monotonic() - started) * 1000)
            if soc1_local is not None and (_pass_child_count > 0 or duration_ms >= SOC1_SLOW_PASS_MS):
                _prepend_buffer(
                    {
                        "op": "insert",
                        "local_id": soc1_local,
                        "line": station_line("SOC1"),
                        "station": "SOC1",
                        "sequence_id": None,
                        "parent_run_id": None,
                        "parent_local_id": None,
                        "edge_id": None,
                        "trigger": "schedule",
                        "cause": None,
                        "subject_ref": None,
                        "blocked_by": None,
                        "waiting_on": None,
                        "outcome": "ok" if _pass_child_count > 0 else "nothing",
                        "started_at": started_at,
                        "finished_at": _utcnow(),
                        "heartbeat_at": started_at,
                        "duration_ms": duration_ms,
                    }
                )
        except Exception:
            _log_throttled("metro_pass", "metro_pass finalisation SOC1 ignorée.", exc_info=True)
        if is_pass_collection_active():
            try:
                _account_hourly_pass(duration_ms, _pass_child_count == 0, started_at)
            except Exception:
                _log_throttled("metro_pass_hourly", "SOC1 horaire ignoré.", exc_info=True)
        _set_current_run_local(None)


def maybe_purge_old_task_runs(db: Session, *, now: datetime | None = None) -> int:
    global _last_purge_monotonic
    try:
        mono = time.monotonic()
        if mono - _last_purge_monotonic < PURGE_MIN_INTERVAL_S:
            return 0
        moment = now or _utcnow()
        cutoff = moment - timedelta(days=RETENTION_DAYS)
        result = db.execute(delete(TaskRun).where(TaskRun.started_at < cutoff))
        _last_purge_monotonic = mono
        return int(result.rowcount or 0)
    except Exception:
        _log_throttled("purge", "Purge task_run ignorée.", exc_info=True)
        return 0


def safe_instrument(fn: Callable[..., None], *args: Any, **kwargs: Any) -> None:
    try:
        fn(*args, **kwargs)
    except Exception:
        logger.debug("Instrumentation task_run ignorée.", exc_info=True)


def dropped_buffer_rows() -> int:
    return _dropped_buffer_rows


def pending_buffer_size() -> int:
    with _LOCK:
        return len(_buffer)


def flush_busy_wait_ms() -> float:
    return _flush_busy_wait_ms


def dropped_flush_batches() -> int:
    return _dropped_flush_batches


def reset_flush_metrics() -> None:
    global _flush_busy_wait_ms, _dropped_flush_batches
    _flush_busy_wait_ms = 0.0
    _dropped_flush_batches = 0

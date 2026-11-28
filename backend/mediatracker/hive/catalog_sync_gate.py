"""Déclenchement conditionnel de ``sync_hive_catalog`` dans le worker (RUC2).

Par défaut, une passe sans changement local ni filet d'intervalle évite le balayage
complet des épisodes. Intervalle de resynchronisation complète : 6 h (override
``hive_catalog_full_sync_interval_sec`` via PATCH /api/v1/settings).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from mediatracker.db.models import Setting

HIVE_CATALOG_WORKER_SYNC_STATE_KEY = "hive_catalog_worker_sync_state"
HIVE_CATALOG_FULL_SYNC_INTERVAL_KEY = "hive_catalog_full_sync_interval_sec"
DEFAULT_FULL_SYNC_INTERVAL_SECONDS = 6 * 60 * 60

_worker_process_needs_initial_full_sync = True


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_state(db: Session) -> dict[str, int | str]:
    row = db.get(Setting, HIVE_CATALOG_WORKER_SYNC_STATE_KEY)
    if row is None or not (row.value or "").strip():
        return {"generation": 0, "applied_generation": 0}
    try:
        raw = json.loads(row.value)
    except json.JSONDecodeError:
        return {"generation": 0, "applied_generation": 0}
    if not isinstance(raw, dict):
        return {"generation": 0, "applied_generation": 0}
    generation = raw.get("generation", 0)
    applied = raw.get("applied_generation", 0)
    out: dict[str, int | str] = {
        "generation": int(generation) if isinstance(generation, int) else 0,
        "applied_generation": int(applied) if isinstance(applied, int) else 0,
    }
    last_full = raw.get("last_full_at")
    if isinstance(last_full, str) and last_full.strip():
        out["last_full_at"] = last_full
    return out


def _save_state(db: Session, state: dict[str, int | str]) -> None:
    text = json.dumps(state, separators=(",", ":"), sort_keys=True)
    row = db.get(Setting, HIVE_CATALOG_WORKER_SYNC_STATE_KEY)
    if row is None:
        db.add(Setting(key=HIVE_CATALOG_WORKER_SYNC_STATE_KEY, value=text))
    else:
        row.value = text
    db.flush()


def full_sync_interval_seconds(db: Session) -> int:
    row = db.get(Setting, HIVE_CATALOG_FULL_SYNC_INTERVAL_KEY)
    if row is None or not (row.value or "").strip():
        return DEFAULT_FULL_SYNC_INTERVAL_SECONDS
    try:
        parsed = int(row.value.strip())
    except ValueError:
        return DEFAULT_FULL_SYNC_INTERVAL_SECONDS
    return max(60, parsed)


def mark_hive_catalog_sync_needed(db: Session) -> None:
    """Incrémente la génération : prochaine passe worker fera un balayage complet."""
    state = _load_state(db)
    state["generation"] = int(state.get("generation", 0)) + 1
    _save_state(db, state)


def generation_for_full_sync_start(db: Session) -> int:
    """Génération au début du balayage (avant toute republication locale)."""
    return int(_load_state(db).get("generation", 0))


def record_hive_catalog_full_sync(db: Session, *, now: datetime, applied_generation: int) -> None:
    state = _load_state(db)
    state["applied_generation"] = int(applied_generation)
    state["last_full_at"] = _utc(now).isoformat().replace("+00:00", "Z")
    _save_state(db, state)


def worker_startup_full_catalog_sync_pending() -> bool:
    """Vrai tant que ce processus worker n'a pas encore terminé un balayage complet."""
    return _worker_process_needs_initial_full_sync


def ack_worker_startup_full_catalog_sync() -> None:
    """Consomme le drapeau de démarrage après un balayage complet réussi."""
    global _worker_process_needs_initial_full_sync
    _worker_process_needs_initial_full_sync = False


def should_run_full_hive_catalog_sync(db: Session, *, now: datetime) -> bool:
    """Vrai si le worker doit exécuter le balayage complet des épisodes."""
    state = _load_state(db)
    generation = int(state.get("generation", 0))
    applied = int(state.get("applied_generation", 0))
    if generation != applied:
        return True
    raw_last = state.get("last_full_at")
    if not isinstance(raw_last, str) or not raw_last.strip():
        return True
    try:
        last_full = _utc(datetime.fromisoformat(raw_last.replace("Z", "+00:00")))
    except ValueError:
        return True
    moment = _utc(now)
    if moment < last_full:
        return True
    interval = timedelta(seconds=full_sync_interval_seconds(db))
    return moment - last_full >= interval
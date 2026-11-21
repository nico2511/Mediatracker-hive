"""Codes 429 pair Hive et libellés côté demandeur."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mediatracker.hive.transfer_settings import hive_instance_timezone

HIVE_BUSY_REASON_HEADER = "Hive-Busy-Reason"

REASON_QUOTA = "quota"
REASON_WINDOW = "window"
REASON_PARALLEL = "parallel"
REASON_BANDWIDTH = "bandwidth"
REASON_RATE = "rate"

DEFAULT_RETRY_PARALLEL = 30
DEFAULT_RETRY_BANDWIDTH = 30
DEFAULT_RETRY_RATE = 60

_WEEKDAYS_FR = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
)


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def format_paris_resume_short(moment: datetime) -> str:
    """Ex. « lundi 01:00 » en heure de Paris."""
    local = _aware_utc(moment).astimezone(hive_instance_timezone())
    wd = _WEEKDAYS_FR[local.weekday()]
    return f"{wd} {local.strftime('%H:%M')}"


def hive_busy_user_message(
    reason: str | None,
    *,
    peer_label: str,
    resume_at: datetime | None = None,
) -> str:
    """Message affiché dans la file de téléchargements ; « occupée » si raison absente."""
    if not reason:
        from mediatracker.hive.download_aria2 import HIVE_PEER_BUSY_MESSAGE

        return HIVE_PEER_BUSY_MESSAGE
    label = (peer_label or "le pair").strip() or "le pair"
    if reason == REASON_QUOTA:
        when = format_paris_resume_short(resume_at) if resume_at else "plus tard"
        return f"Quota hebdomadaire atteint chez {label}, reprise {when}"
    if reason == REASON_WINDOW:
        when = format_paris_resume_short(resume_at) if resume_at else "à l'ouverture du créneau"
        return f"Plage horaire fermée chez {label}, reprise {when}"
    if reason == REASON_PARALLEL:
        return f"Envois parallèles en cours chez {label}."
    if reason == REASON_BANDWIDTH:
        return f"Débit d'envoi limité chez {label}."
    if reason == REASON_RATE:
        return f"Limite de requêtes atteinte chez {label}."
    from mediatracker.hive.download_aria2 import HIVE_PEER_BUSY_MESSAGE

    return HIVE_PEER_BUSY_MESSAGE


def retry_after_timedelta(seconds: float | None, *, cap: timedelta | None = None) -> timedelta | None:
    if seconds is None:
        return None
    delay = timedelta(seconds=max(0.0, float(seconds)))
    if cap is not None and delay > cap:
        return cap
    return delay
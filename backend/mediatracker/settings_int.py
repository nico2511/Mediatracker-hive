"""Lecture d’un réglage entier en base avec repli sur la config applicative."""

from __future__ import annotations

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting


def int_setting(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def trash_retention_days(db: Session, settings: Settings) -> int:
    return int_setting(db, "trash_retention_days", settings.trash_retention_days)

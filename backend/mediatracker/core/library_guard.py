"""Suspend library absence updates when the volume looks unmounted or mostly gone.

A scan or grouped presence pass must not mark the whole library missing when the
disk is empty or when too many files vanish at once. While suspended, absence
side effects stay off until a normal scan clears the guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import LibraryFile, Setting
from mediatracker.events import record_event

logger = logging.getLogger(__name__)

LIBRARY_SUSPENDED_KEY = "system.library_scan_suspended"
MASS_ABSENCE_FRACTION = 0.20


@dataclass(frozen=True)
class AbsenceAssessment:
    known: int
    absent: int
    reason: str | None  # empty_root | mass_absence


def is_suspended(db: Session) -> bool:
    row = db.get(Setting, LIBRARY_SUSPENDED_KEY)
    return row is not None and row.value == "1"


def absence_updates_allowed(db: Session) -> bool:
    return not is_suspended(db)


def assess_grouped_absence(
    db: Session,
    *,
    roots: tuple[Path, Path],
    seen: set[str],
) -> AbsenceAssessment:
    """Measure how many indexed files are absent from this scan pass."""
    existing_roots = [_root_path(root) for root in roots if root.is_dir()]
    scope_roots = existing_roots or [_root_path(root) for root in roots]
    rows = _active_rows(db, scope_roots)
    known = len(rows)
    absent = sum(1 for row in rows if row.path not in seen)
    reason = None
    if known > 0:
        if not existing_roots:
            reason = "empty_root"
        elif len(seen) == 0:
            reason = "empty_root"
        elif absent > int(known * MASS_ABSENCE_FRACTION):
            reason = "mass_absence"
    return AbsenceAssessment(known=known, absent=absent, reason=reason)


def assess_scoped_absence(
    db: Session,
    *,
    folders: tuple[Path, ...],
    seen: set[str],
) -> AbsenceAssessment:
    """Measure absence for library rows under the given folders only."""
    resolved = [_root_path(folder) for folder in folders if folder.is_dir()]
    scope_roots = resolved or [_root_path(folder) for folder in folders]
    rows = _active_rows(db, scope_roots)
    scoped = [row for row in rows if any(_is_under(Path(row.path), root) for root in scope_roots)]
    known = len(scoped)
    absent = sum(1 for row in scoped if row.path not in seen)
    reason = None
    if known > 0:
        if not resolved:
            reason = "empty_root"
        elif len(seen) == 0:
            reason = "empty_root"
        elif absent > int(known * MASS_ABSENCE_FRACTION):
            reason = "mass_absence"
    return AbsenceAssessment(known=known, absent=absent, reason=reason)


def reconcile_scoped_absence(
    db: Session,
    *,
    seen: set[str],
    folders: tuple[Path, ...],
    now: datetime,
    mark_absent,
) -> int:
    """Apply absence updates for rows under ``folders``, or enter suspension."""
    assessment = assess_scoped_absence(db, folders=folders, seen=seen)
    if assessment.reason is not None:
        _enter_suspension(db, assessment, now)
        return 0
    if is_suspended(db):
        _leave_suspension(db, now)
    scoped_seen = seen
    scoped_roots = tuple(_root_path(folder) for folder in folders)
    return mark_absent(db, scoped_seen, scoped_roots, now, scope_roots=[_root_path(folder) for folder in folders])


def reconcile_grouped_absence(
    db: Session,
    *,
    seen: set[str],
    roots: tuple[Path, Path],
    now: datetime,
    mark_absent,
) -> int:
    """Apply absence updates or enter suspension. ``mark_absent`` marks rows when allowed."""
    assessment = assess_grouped_absence(db, roots=roots, seen=seen)
    if assessment.reason is not None:
        _enter_suspension(db, assessment, now)
        return 0
    if is_suspended(db):
        _leave_suspension(db, now)
    return mark_absent(db, seen, roots, now)


def _enter_suspension(db: Session, assessment: AbsenceAssessment, now: datetime) -> None:
    if not _flag(db):
        record_event(
            db,
            "library.scan_suspended",
            level="warning",
            when=now,
            payload={
                "title": "Bibliothèque",
                "reason": assessment.reason,
                "known": assessment.known,
                "absent": assessment.absent,
                "threshold": MASS_ABSENCE_FRACTION,
                "level": "warning",
            },
        )
        logger.info(
            "Scan bibliothèque suspendu (%s) : %s fichier(s) connus, %s absents du scan.",
            assessment.reason,
            assessment.known,
            assessment.absent,
        )
    _mark(db, True, now)


def _leave_suspension(db: Session, now: datetime) -> None:
    record_event(
        db,
        "library.scan_resumed",
        when=now,
        payload={
            "title": "Bibliothèque",
            "message": "Le scan bibliothèque reprend après une suspension.",
        },
    )
    _mark(db, False, now)
    logger.info("Scan bibliothèque repris après suspension.")


def _active_rows(db: Session, roots: list[Path]) -> list[LibraryFile]:
    if not roots:
        roots = []
    resolved_roots = [_root_path(root) for root in roots]
    rows = list(db.scalars(select(LibraryFile).where(LibraryFile.status.not_in(("missing", "trashed")))).all())
    if not resolved_roots:
        return rows
    kept: list[LibraryFile] = []
    for row in rows:
        path = Path(row.path)
        if any(_is_under(path, root) for root in resolved_roots):
            kept.append(row)
    return kept


def _root_path(root: Path) -> Path:
    try:
        return root.resolve()
    except OSError:
        return root


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _flag(db: Session) -> bool:
    return is_suspended(db)


def _mark(db: Session, suspended: bool, now: datetime) -> None:
    row = db.get(Setting, LIBRARY_SUSPENDED_KEY)
    value = "1" if suspended else "0"
    if row is None:
        if not suspended:
            return
        db.add(Setting(key=LIBRARY_SUSPENDED_KEY, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now

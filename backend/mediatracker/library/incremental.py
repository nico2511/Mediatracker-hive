"""Incremental library scan via folder mtimes (local disks only)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.library_guard import reconcile_scoped_absence
from mediatracker.core.local_fs import is_local_filesystem
from mediatracker.core.storage import is_under
from mediatracker.db.models import LibraryFolderMtime
from mediatracker.events import record_event
from mediatracker.library.parse import season_number
from mediatracker.library.scanner import ScanReport, _ingest, _iter_videos, _mark_absent_scoped


def iter_tracked_folders(root: Path, root_kind: str, skip: tuple[Path, ...]) -> list[Path]:
    """Show folders under a library root, plus season folders for series."""
    if not root.is_dir() or is_under(root, skip):
        return []
    folders: list[Path] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith(".") or is_under(child, skip):
            continue
        folders.append(child.resolve())
        if root_kind != "series":
            continue
        for sub in sorted(child.iterdir(), key=lambda item: item.name):
            if not sub.is_dir() or sub.name.startswith(".") or is_under(sub, skip):
                continue
            if season_number(sub.name) is not None:
                folders.append(sub.resolve())
    return folders


def folder_mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def folder_needs_scan(db: Session, path: Path) -> bool:
    mtime = folder_mtime_ns(path)
    if mtime is None:
        return False
    key = str(path)
    row = db.get(LibraryFolderMtime, key)
    if row is None:
        return True
    return row.mtime_ns != mtime


def write_mtime_if_changed(db: Session, path: Path) -> bool:
    mtime = folder_mtime_ns(path)
    if mtime is None:
        return False
    key = str(path)
    row = db.get(LibraryFolderMtime, key)
    if row is not None and row.mtime_ns == mtime:
        return False
    if row is None:
        db.add(LibraryFolderMtime(path=key, mtime_ns=mtime))
    else:
        row.mtime_ns = mtime
    return True


def refresh_all_folder_mtimes(
    db: Session,
    *,
    movies: Path,
    series: Path,
    skip: tuple[Path, ...],
) -> None:
    for root, kind in ((movies, "film"), (series, "series")):
        if not is_local_filesystem(root):
            continue
        for folder in iter_tracked_folders(root, kind, skip):
            write_mtime_if_changed(db, folder)


def collect_changed_folders(
    db: Session,
    *,
    movies: Path,
    series: Path,
    skip: tuple[Path, ...],
) -> list[Path]:
    changed: list[Path] = []
    for root, kind in ((movies, "film"), (series, "series")):
        if not is_local_filesystem(root):
            continue
        for folder in iter_tracked_folders(root, kind, skip):
            if folder_needs_scan(db, folder):
                changed.append(folder)
    return changed


def _scan_context(folder: Path, movies: Path, series: Path) -> tuple[Path, str] | None:
    resolved = folder.resolve()
    movies_root = movies.resolve()
    series_root = series.resolve()
    try:
        resolved.relative_to(movies_root)
        return movies_root, "film"
    except ValueError:
        pass
    try:
        resolved.relative_to(series_root)
        return series_root, "series"
    except ValueError:
        return None


def scan_library_incremental(
    db: Session,
    *,
    movies: Path,
    series: Path,
    folders: list[Path],
    now: datetime | None = None,
    tmdb=None,
    skip: tuple[Path, ...] = (),
    settings: Settings | None = None,
) -> ScanReport:
    from mediatracker.core.background_priority import apply_background_priority

    apply_background_priority(process_wide=False)
    moment = now or datetime.now(timezone.utc)
    seen: set[str] = set()
    matched = review = unmatched = followed = 0
    for folder in folders:
        context = _scan_context(folder, movies, series)
        if context is None:
            continue
        root, kind = context
        for path in _iter_videos(folder, skip):
            key = str(path.resolve())
            seen.add(key)
            from mediatracker.library.parse import parse_path

            parsed = parse_path(path, root=root, root_kind=kind)
            outcome, created = _ingest(db, path, parsed, moment, tmdb)
            followed += created
            if outcome == "matched":
                matched += 1
            elif outcome == "review":
                review += 1
            else:
                unmatched += 1
    missing = reconcile_scoped_absence(
        db,
        seen=seen,
        folders=tuple(folders),
        now=moment,
        mark_absent=_mark_absent_scoped,
    )
    for folder in folders:
        write_mtime_if_changed(db, folder)
    record_event(
        db,
        "library.scanned",
        payload={
            "seen": len(seen),
            "matched": matched,
            "review": review,
            "unmatched": unmatched,
            "missing": missing,
            "followed": followed,
            "incremental": True,
        },
    )
    if settings is not None:
        from mediatracker.core.library_claims import reconcile_library_claims_after_scan

        reconcile_library_claims_after_scan(db, settings, moment)
    return ScanReport(
        seen=len(seen),
        matched=matched,
        review=review,
        unmatched=unmatched,
        missing=missing,
        followed=followed,
    )

"""Folder sizes for the disk card. The walk never runs on the request thread.

The cache is the last finished measurement. A refresh starts one background
pass; opening the screen returns that cache at once, even when a new pass is
still counting a large library.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.incomplete import busy_names, download_linked_basenames, incomplete_disk_orphan
from mediatracker.core.storage import device_id, incomplete_dir, storage_layout
from mediatracker.db.models import Download, Setting

logger = logging.getLogger(__name__)

CACHE_KEY = "disk.folder_usage"
STALE_AFTER = timedelta(hours=6)
_FOLDERS = (
    ("movies", "Films"),
    ("series", "Séries"),
    ("incomplete", "Téléchargements incomplets"),
    ("trash", "Corbeille"),
)

_lock = threading.Lock()
_running = False
_memory: dict | None = None
_LARGEST = 8
_BLOCK = 512
_JOB_RANK = {
    "downloading": 0,
    "queued": 1,
    "post_processing": 2,
    "resolving": 3,
    "pending": 4,
    "failed": 5,
    "cancelled": 6,
    "done": 7,
}


def reset_state() -> None:
    """Drop the in-memory cache. Tests call this so one case cannot leak into the next."""
    global _running, _memory
    with _lock:
        _running = False
        _memory = None


def snapshot(db: Session) -> dict:
    """Last result, immediately. ``running`` is true while a pass is counting."""
    global _memory
    with _lock:
        running = _running
        if _memory is None:
            _memory = _load(db)
        data = dict(_memory or _empty())
    data["running"] = running
    return data


def schedule(factory, settings: Settings, *, force: bool) -> None:
    """Start one pass unless one is already running. Returns before the walk."""
    global _running
    with _lock:
        if _running:
            return
        if not force and _memory is not None and not needs_refresh(_memory):
            return
        _running = True
    threading.Thread(
        target=_run,
        args=(factory, settings),
        name="disk-usage",
        daemon=True,
    ).start()


def needs_refresh(report: dict, *, now: datetime | None = None) -> bool:
    if report.get("computed_at") is None:
        return True
    moment = now or datetime.now(timezone.utc)
    try:
        last = datetime.fromisoformat(str(report["computed_at"]))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return moment - last >= STALE_AFTER


def measure_paths(
    paths: dict[str, Path],
    *,
    now: datetime,
    walk=None,
    usage=None,
    device=None,
    mount=None,
    jobs: dict[str, dict] | None = None,
    busy: set[str] | None = None,
) -> dict:
    """Count the four folders and the volume each one sits on.

    ``bytes`` and ``written_bytes`` are allocated blocks (what ``du`` reports).
    ``reserved_bytes`` is the logical length (``st_size``), which aria2 sets to
    the full file as soon as it preallocates. Symlinks are skipped. A hard link
    is counted once.
    """
    walker = walk or walk_tree
    usage_of = usage or _disk_usage
    device_of = device or device_id
    mount_of = mount or mount_point
    known = jobs or {}
    active = busy if busy is not None else set()
    folders: list[dict] = []
    for key, label in _FOLDERS:
        path = paths[key]
        written, reserved, missing, files = walker(path)
        dev = device_of(path)
        folders.append(
            {
                "key": key,
                "label": label,
                "path": str(path),
                "bytes": written,
                "written_bytes": written,
                "reserved_bytes": reserved,
                "missing": missing,
                "device": "" if dev is None else str(dev),
                "mount": mount_of(path),
                "files": _with_jobs(files, known, active) if key == "incomplete" else [],
            }
        )
    volumes = _volumes(folders, usage_of)
    guard = next((row for row in volumes if _on_volume(row, "incomplete", folders)), None)
    if guard is None and volumes:
        guard = volumes[0]
    return {
        "computed_at": now.isoformat(),
        "error": "",
        "free_bytes": None if guard is None else guard["free_bytes"],
        "folders": folders,
        "volumes": volumes,
    }


def file_allocation(path: Path) -> tuple[int, int]:
    """Allocated blocks and logical size for one regular file."""
    try:
        info = path.lstat()
    except OSError:
        return 0, 0
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        return 0, 0
    written = max(int(info.st_blocks) * _BLOCK, 0)
    reserved = max(int(info.st_size), 0)
    return written, reserved


def walk_tree(path: Path) -> tuple[int, int, bool, list[dict]]:
    """Allocated bytes, logical bytes, missing flag, and the largest files.

    The top directory may itself be a link. Links inside it are not followed
    and are not added. ``.aria2`` control files and subdirectories are counted
    once each, never twice.
    """
    if not path.exists():
        return 0, 0, True, []
    root = path.resolve()
    written = 0
    reserved = 0
    largest: list[dict] = []
    seen: set[tuple[int, int]] = set()
    if root.is_file():
        added = _add_file(root, root.parent, seen)
        if added is None:
            return 0, 0, False, []
        return added[0], added[1], False, [added[2]]
    for current, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            added = _add_file(Path(current) / name, root, seen)
            if added is None:
                continue
            written += added[0]
            reserved += added[1]
            _keep_largest(largest, added[2])
    return written, reserved, False, largest


def download_index(db: Session) -> dict[str, dict]:
    """Best live job for each incomplete filename. An older finished row loses."""
    rows = list(db.scalars(select(Download)).all())
    best: dict[str, Download] = {}
    for row in rows:
        for name in download_linked_basenames(row):
            current = best.get(name)
            if current is None or _job_key(row) < _job_key(current):
                best[name] = row
    return {name: {"job_id": row.id, "job_state": row.state} for name, row in best.items()}


def _job_key(row: Download) -> tuple[int, int]:
    return (_JOB_RANK.get(row.state, 9), -row.id)


def _add_file(path: Path, root: Path, seen: set[tuple[int, int]]) -> tuple[int, int, dict] | None:
    if path.is_symlink():
        return None
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    ident = (info.st_dev, info.st_ino)
    if info.st_ino and ident in seen:
        return None
    if info.st_ino:
        seen.add(ident)
    written = max(int(info.st_blocks) * _BLOCK, 0)
    reserved = max(int(info.st_size), 0)
    try:
        name = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        name = path.name
    return written, reserved, {"name": name, "written_bytes": written, "reserved_bytes": reserved}


def _keep_largest(largest: list[dict], item: dict) -> None:
    largest.append(item)
    largest.sort(key=lambda row: (row["reserved_bytes"], row["written_bytes"], row["name"]), reverse=True)
    del largest[_LARGEST:]


def _with_jobs(files: list[dict], jobs: dict[str, dict], busy: set[str]) -> list[dict]:
    labeled: list[dict] = []
    for row in files:
        display = str(row["name"])
        base = Path(display).name
        lookup = base[: -len(".aria2")] if base.endswith(".aria2") else base
        job = jobs.get(lookup) or jobs.get(base)
        labeled.append(
            {
                "name": row["name"],
                "written_bytes": row["written_bytes"],
                "reserved_bytes": row["reserved_bytes"],
                "job_id": None if job is None else job["job_id"],
                "job_state": "" if job is None else job["job_state"],
                "orphan": incomplete_disk_orphan(display, busy),
            }
        )
    return labeled


def mount_point(path: Path) -> str:
    current = path
    try:
        current = path.resolve()
    except OSError:
        pass
    while True:
        try:
            if os.path.ismount(current):
                return str(current)
        except OSError:
            return str(current)
        if current.parent == current:
            return str(current)
        current = current.parent


def _run(factory, settings: Settings) -> None:
    global _running, _memory
    try:
        db = factory()
        try:
            layout = storage_layout(db, settings)
            paths = _layout_paths(layout)
            jobs = download_index(db)
            busy = busy_names(db)
        finally:
            db.close()
        report = measure_paths(paths, now=datetime.now(timezone.utc), jobs=jobs, busy=busy)
        db = factory()
        try:
            _save(db, report, datetime.now(timezone.utc))
        finally:
            db.close()
        with _lock:
            _memory = report
        logger.info("Taille des dossiers calculée.")
    except Exception:
        logger.exception("Calcul de la taille des dossiers échoué.")
        with _lock:
            base = dict(_memory or _empty())
            base["error"] = "Calcul impossible."
            _memory = base
    finally:
        with _lock:
            _running = False


def _layout_paths(layout) -> dict[str, Path]:
    return {
        "movies": layout.movies_dir,
        "series": layout.series_dir,
        "incomplete": incomplete_dir(layout),
        "trash": layout.trash_dir,
    }


def _volumes(folders: list[dict], usage_of) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for folder in folders:
        groups.setdefault(folder["device"] or "", []).append(folder)
    volumes: list[dict] = []
    for device, rows in groups.items():
        sample = Path(rows[0]["path"])
        info = None if not device else usage_of(sample)
        measured = sum(row["bytes"] for row in rows)
        if info is None:
            total = used = free = 0
        else:
            total, used, free = int(info.total), int(info.used), int(info.free)
        other = used - measured
        volumes.append(
            {
                "device": device,
                "mount": rows[0]["mount"],
                "total_bytes": total,
                "used_bytes": used,
                "free_bytes": free,
                "measured_bytes": measured,
                "other_bytes": other,
                "folders": [row["label"] for row in rows],
            }
        )
    return volumes


def _on_volume(volume: dict, key: str, folders: list[dict]) -> bool:
    folder = next(row for row in folders if row["key"] == key)
    return volume["device"] == (folder["device"] or "")


def _disk_usage(path: Path):
    current = path
    while True:
        try:
            if current.exists():
                return shutil.disk_usage(current)
        except OSError:
            return None
        if current.parent == current:
            return None
        current = current.parent


def _empty() -> dict:
    return {"computed_at": None, "error": "", "free_bytes": None, "folders": [], "volumes": []}


def _load(db: Session) -> dict | None:
    row = db.get(Setting, CACHE_KEY)
    if row is None or not row.value:
        return None
    try:
        data = json.loads(row.value)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save(db: Session, report: dict, now: datetime) -> None:
    payload = json.dumps(report, ensure_ascii=False)
    row = db.get(Setting, CACHE_KEY)
    if row is None:
        db.add(Setting(key=CACHE_KEY, value=payload, is_secret=False, updated_at=now))
    else:
        row.value = payload
        row.updated_at = now
    db.commit()

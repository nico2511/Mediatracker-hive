"""Preview, then trash, leftover files in the incomplete download directory.

An orphan is a file with no active or queued download. If that episode or film
is already in the library, it goes to the trash immediately. Other orphans stay
for a few days. Tiny files and stray ``.aria2`` control files are included.
Nothing is unlinked: the trash purge does that later.
"""

from __future__ import annotations

import errno
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.cleanup import _int_setting, format_bytes
from mediatracker.core.naming import atomic_move
from mediatracker.core.storage import incomplete_dir, is_under, library_skip, storage_layout
from mediatracker.db.models import Download, Setting, TrashEntry
from mediatracker.events import record_event
from mediatracker.library.parse import is_video, parse_release, show_key
from mediatracker.retention import INCOMPLETE_ORPHAN_KEEP_DAYS

logger = logging.getLogger(__name__)

KEEP_DAYS = INCOMPLETE_ORPHAN_KEEP_DAYS
TINY_BYTES = 64 * 1024
BUSY = ("pending", "resolving", "queued", "downloading", "post_processing")
_STAMP = "incomplete_cleanup_at"
_STATUS = "incomplete_cleanup_status"
_EVENT = "incomplete.cleaned"
_STARTUP_EVENT = "cleanup.incomplete_orphans"
_KEEP_ACTIVE = "téléchargement en cours"
_KEEP_ARIA = "contrôle aria2 d'un téléchargement en cours"
_KEEP_RECENT = f"moins de {KEEP_DAYS} jours"
_NEVER = "Nettoyage automatique activé. Aucun passage encore (au plus une fois par heure)."
_LOCKED = {errno.EBUSY, errno.ETXTBSY, errno.EAGAIN}
_ARIA2_SUFFIX_DUPLICATE = re.compile(r"\.mkv\.\d+\.mkv$", re.IGNORECASE)
_ARIA2_BEFORE_EXT_DUPLICATE = re.compile(r"^(?P<stem>.+)\.(?P<idx>\d+)\.mkv$", re.IGNORECASE)


@dataclass(frozen=True)
class IncompleteFile:
    path: str
    name: str
    size: int
    reason: str
    written_bytes: int = 0
    reserved_bytes: int = 0
    job_id: int | None = None
    job_state: str = ""
    orphan: bool = True


@dataclass(frozen=True)
class IncompleteApply:
    """What remains in incomplete after the call, and where the moved files went."""

    files: tuple[IncompleteFile, ...]
    moved: int
    failed: int
    errors: tuple[str, ...]
    trash: str
    trash_ids: tuple[int, ...] = ()
    folder_written_bytes: int = 0
    folder_reserved_bytes: int = 0
    total_written_bytes: int = 0


@dataclass(frozen=True)
class CleanupReport:
    """Last automatic pass, for the library screen. The file list is always recomputed."""

    enabled: bool
    at: datetime | None
    message: str


@dataclass(frozen=True)
class IncompleteSnapshot:
    """Trash candidates plus folder totals aligned with the disk card."""

    files: tuple[IncompleteFile, ...]
    folder_written_bytes: int
    folder_reserved_bytes: int
    total_written_bytes: int


def preview_incomplete(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> list[IncompleteFile]:
    return list(incomplete_snapshot(db, settings, now=now).files)


def incomplete_snapshot(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> IncompleteSnapshot:
    files, _kept, folder_written, folder_reserved = _survey(db, settings, now=now)
    total = sum(item.written_bytes for item in files)
    return IncompleteSnapshot(tuple(files), folder_written, folder_reserved, total)


def apply_incomplete(
    db: Session,
    settings: Settings,
    *,
    validated: bool,
    paths: tuple[str, ...] | list[str] = (),
    now: datetime | None = None,
) -> IncompleteApply:
    """Move the preview to the trash when ``validated`` is set.

    One file that cannot move does not cancel the others. The returned files
    are what is still in the incomplete folder, not the list from before the move.
    """
    layout = storage_layout(db, settings)
    trash = layout.trash_dir
    before = incomplete_snapshot(db, settings, now=now)
    plan = list(before.files)
    if paths:
        wanted = {str(Path(item).resolve()) for item in paths}
        plan = [item for item in plan if item.path in wanted]
    if not validated:
        return IncompleteApply(
            before.files,
            0,
            0,
            (),
            str(trash),
            (),
            before.folder_written_bytes,
            before.folder_reserved_bytes,
            before.total_written_bytes,
        )
    moment = now or datetime.now(timezone.utc)
    moved = 0
    errors: list[str] = []
    trash_ids: list[int] = []
    for item in plan:
        src = Path(item.path)
        if not src.is_file():
            continue
        dest = trash / moment.strftime("%Y%m%dT%H%M%S") / src.name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}-{src.stat().st_ino}{dest.suffix}")
            original = str(src.resolve())
            atomic_move(src, dest)
        except OSError as exc:
            if "FILE_MISSING" in str(exc):
                continue
            errors.append(_move_error(item.name, exc))
            continue
        entry = TrashEntry(
            original_path=original,
            trash_path=str(dest.resolve()),
            size=item.written_bytes or item.size,
            label=item.name[:300],
            reason="incomplete",
            deleted_at=moment,
            purge_after=moment + timedelta(days=_retention_days(db, settings)),
        )
        db.add(entry)
        db.flush()
        trash_ids.append(entry.id)
        moved += 1
    after = incomplete_snapshot(db, settings, now=moment)
    return IncompleteApply(
        after.files,
        moved,
        len(errors),
        tuple(errors),
        str(trash),
        tuple(trash_ids),
        after.folder_written_bytes,
        after.folder_reserved_bytes,
        after.total_written_bytes,
    )


def maybe_clean_incomplete(db: Session, settings: Settings, *, now: datetime | None = None) -> int:
    """Optional automatic pass, at most once an hour.

    Same rules as the manual button. Each pass writes an ``incomplete.cleaned``
    event and a short status the library screen can show. A move that fails is
    counted and explained; the file stays in the folder.
    """
    if not _enabled(db):
        return 0
    moment = now or datetime.now(timezone.utc)
    if _too_soon(db, moment):
        return 0
    result = apply_incomplete(db, settings, validated=True, now=moment)
    _kept_files, kept, _folder_written, _folder_reserved = _survey(db, settings, now=moment)
    del _kept_files, _folder_written, _folder_reserved
    message = describe_pass(moment, result.moved, result.failed, kept, result.errors)
    record_event(
        db,
        _EVENT,
        level="warning" if result.failed else "info",
        payload={
            "moved": result.moved,
            "failed": result.failed,
            "kept": sum(kept.values()),
            "kept_reasons": kept,
            "errors": list(result.errors),
            "message": message,
        },
        when=moment,
    )
    _remember(db, moment, message)
    logger.info("%s", message)
    db.commit()
    return result.moved


def cleanup_report(db: Session) -> CleanupReport:
    """What the last automatic pass did. Disabled means the screen stays on the manual hint."""
    enabled = _enabled(db)
    status = _read_status(db)
    if not enabled:
        return CleanupReport(False, status[0] if status else None, "")
    if status is not None:
        return CleanupReport(True, status[0], status[1])
    stamp = _read_stamp(db)
    if stamp is not None:
        text = f"Dernier passage le {stamp.strftime('%Y-%m-%d %H:%M UTC')}, sans détail enregistré."
        return CleanupReport(True, stamp, text)
    return CleanupReport(True, None, _NEVER)


def describe_pass(
    moment: datetime,
    moved: int,
    failed: int,
    kept: dict[str, int],
    errors: tuple[str, ...] | list[str],
) -> str:
    stamp = moment.strftime("%Y-%m-%d %H:%M UTC")
    text = f"Passage automatique du {stamp} : {moved} déplacé(s), {failed} échec(s), {sum(kept.values())} laissé(s)."
    if kept:
        details = ", ".join(f"{count} × {reason}" for reason, count in sorted(kept.items()))
        text = f"{text} Laissés : {details}."
    if errors:
        text = f"{text} {' '.join(errors)}"
    return text


def _retention_days(db: Session, settings: Settings) -> int:
    return _int_setting(db, "trash_retention_days", settings.trash_retention_days)


def _enabled(db: Session) -> bool:
    row = db.get(Setting, "incomplete_cleanup_enabled")
    return row is not None and row.value == "true"


def download_linked_basenames(row: Download) -> list[str]:
    """Basenames on disk that still belong to this download row."""
    names: list[str] = []
    if row.filename:
        names.append(Path(row.filename).name)
    if row.state == "post_processing" and row.error:
        names.append(Path(row.error).name)
    return names


def busy_names(db: Session) -> set[str]:
    """Incomplete files tied to a pending or active job, same rule as cleanup."""
    names: set[str] = set()
    rows = db.scalars(select(Download).where(Download.state.in_(BUSY))).all()
    for row in rows:
        names.update(download_linked_basenames(row))
    return names


def prune_incomplete_orphans_at_startup(db: Session, settings: Settings) -> None:
    """One pass at init: trash stray ``.aria2`` and empty files with no active job."""
    moment = datetime.now(timezone.utc)
    layout = storage_layout(db, settings)
    root = incomplete_dir(layout)
    if not root.is_dir():
        return
    busy = busy_names(db)
    trash = layout.trash_dir
    orphans: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.name):
        if not path.is_file() or path.name.startswith("."):
            continue
        if is_under(path, (trash,)):
            continue
        if _startup_orphan(path, busy):
            orphans.append(path)
    if not orphans:
        return
    _forget_incomplete_files(db, settings, orphans)
    moved_paths: list[str] = []
    for src in orphans:
        if not src.is_file():
            continue
        try:
            size = src.stat().st_size
        except OSError:
            continue
        dest = trash / moment.strftime("%Y%m%dT%H%M%S") / src.name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                dest = dest.with_name(f"{dest.stem}-{src.stat().st_ino}{dest.suffix}")
            original = str(src.resolve())
            atomic_move(src, dest)
        except OSError as exc:
            if "FILE_MISSING" in str(exc):
                continue
            logger.info("Orphelin incomplet laissé sur place (%s).", _move_error(src.name, exc))
            continue
        db.add(
            TrashEntry(
                original_path=original,
                trash_path=str(dest.resolve()),
                size=size,
                label=src.name[:300],
                reason="incomplete",
                deleted_at=moment,
                purge_after=moment + timedelta(days=_retention_days(db, settings)),
            )
        )
        moved_paths.append(original)
    if not moved_paths:
        return
    record_event(
        db,
        _STARTUP_EVENT,
        level="info",
        payload={"count": len(moved_paths), "paths": moved_paths},
        when=moment,
    )
    logger.info(
        "Incomplets orphelins au démarrage : %d fichier(s) mis à la corbeille.",
        len(moved_paths),
    )
    db.commit()


def _startup_orphan(path: Path, busy: set[str]) -> bool:
    name = path.name
    if name.endswith(".aria2"):
        video = name[: -len(".aria2")]
        return video not in busy and name not in busy
    if name in busy:
        return False
    if _aria2_suffix_duplicate(path) or _aria2_before_ext_duplicate(path, busy):
        try:
            return path.stat().st_size > 0
        except OSError:
            return False
    try:
        return path.stat().st_size == 0
    except OSError:
        return False


def _aria2_suffix_duplicate(path: Path) -> bool:
    """aria2 collision file such as ``Episode.mkv.1.mkv`` (orphan at startup only)."""
    return is_video(path) and bool(_ARIA2_SUFFIX_DUPLICATE.search(path.name))


def _aria2_collision_index(index: str) -> bool:
    """True when the numeric suffix is an aria2 collision index (1–99), not a release year."""
    if len(index) == 4 and index.startswith(("19", "20")):
        return False
    try:
        value = int(index, 10)
    except ValueError:
        return False
    return 1 <= value <= 99


def _aria2_before_ext_duplicate(path: Path, busy: set[str]) -> bool:
    """aria2 collision file such as ``Episode.1.mkv`` when ``Episode.mkv`` already exists."""
    if not is_video(path):
        return False
    match = _ARIA2_BEFORE_EXT_DUPLICATE.match(path.name)
    if match is None or not _aria2_collision_index(match.group("idx")):
        return False
    base = f"{match.group('stem')}.mkv"
    sibling = path.parent / base
    if not sibling.is_file() and base not in busy:
        return False
    if base in busy:
        return False
    return sibling.is_file()


def _forget_incomplete_files(db: Session, settings: Settings, paths: list[Path]) -> None:
    names = {path.name for path in paths}
    client = None
    try:
        from mediatracker.core.pipeline.clients import _downloader

        _row, client = _downloader(db, settings, None)
    except Exception:
        logger.info("Client aria2 indisponible pour les orphelins incomplets.")
    if client is not None and hasattr(client, "forget_files"):
        try:
            client.forget_files(paths)
        except Exception:
            logger.info("Arrêt aria2 ignoré pour les orphelins incomplets.")
    try:
        from mediatracker.providers.downloaders.aria2 import drop_session_downloads

        drop_session_downloads(Path(settings.config_dir) / "aria2.session", names)
    except Exception:
        logger.info("Session aria2 non mise à jour pour les orphelins incomplets.")


def incomplete_disk_orphan(file_name: str, busy: set[str]) -> bool:
    """Whether the disk card may label the file as orphelin (no active download)."""
    name = Path(file_name).name
    if name.endswith(".aria2"):
        video = name[: -len(".aria2")]
        if video in busy or name in busy:
            return False
        return True
    if name in busy:
        return False
    return True


def _survey(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[list[IncompleteFile], dict[str, int], int, int]:
    """Files the button would trash, and why the others stay. Hidden names are ignored."""
    from mediatracker.core.disk_usage import download_index, file_allocation, walk_tree

    moment = now or datetime.now(timezone.utc)
    layout = storage_layout(db, settings)
    root = incomplete_dir(layout)
    if not root.is_dir():
        return [], {}, 0, 0
    busy = busy_names(db)
    jobs = download_index(db)
    folder_written, folder_reserved, _missing, _largest = walk_tree(root)
    library = _library_index(layout.movies_dir, layout.series_dir, library_skip(layout))
    found: list[IncompleteFile] = []
    kept: dict[str, int] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.name):
        if not path.is_file() or path.name.startswith("."):
            continue
        verdict = _verdict(path, busy, library, moment)
        if verdict is None:
            continue
        action, reason = verdict
        if action == "keep":
            kept[reason] = kept.get(reason, 0) + 1
            continue
        written, reserved = file_allocation(path)
        if written == 0 and reserved == 0:
            continue
        job = _job_for(path.name, jobs)
        found.append(
            IncompleteFile(
                str(path.resolve()),
                path.name,
                written,
                reason,
                written_bytes=written,
                reserved_bytes=reserved,
                job_id=job[0],
                job_state=job[1],
                orphan=True,
            )
        )
    return found, kept, folder_written, folder_reserved


def _job_for(name: str, jobs: dict[str, dict]) -> tuple[int | None, str]:
    base = Path(name).name
    lookup = base[: -len(".aria2")] if base.endswith(".aria2") else base
    job = jobs.get(lookup) or jobs.get(base)
    if job is None:
        return None, ""
    return job["job_id"], job["job_state"]


def _verdict(path: Path, busy: set[str], library: set[tuple], now: datetime) -> tuple[str, str] | None:
    """``trash`` uses the manual rules. ``keep`` explains why the file is still there."""
    name = path.name
    if name.endswith(".aria2"):
        video = name[: -len(".aria2")]
        if video in busy or name in busy:
            return ("keep", _KEEP_ARIA)
        return ("trash", "Contrôle aria2 orphelin.")
    if name in busy:
        return ("keep", _KEEP_ACTIVE)
    try:
        stat = path.stat()
    except OSError:
        return None
    age = now - datetime.fromtimestamp(stat.st_mtime, timezone.utc)
    if _already_in_library(path, library):
        return ("trash", "Déjà dans la bibliothèque.")
    if stat.st_size <= TINY_BYTES:
        return ("trash", f"Fichier minuscule ({format_bytes(stat.st_size)}).")
    if age >= timedelta(days=KEEP_DAYS):
        return ("trash", f"Incomplet depuis plus de {KEEP_DAYS} jours.")
    return ("keep", _KEEP_RECENT)


def _already_in_library(path: Path, library: set[tuple]) -> bool:
    if not is_video(path):
        return False
    parsed = parse_release(path.name)
    key = show_key(parsed.title)
    if not key:
        return False
    if parsed.season is None or parsed.episode is None:
        return ("film", key, None, None) in library
    return ("series", key, parsed.season, parsed.episode) in library


def _move_error(name: str, exc: OSError) -> str:
    if "CROSS_DEVICE" in str(exc):
        return f"{name} : autre disque, laissé sur place."
    if exc.errno in _LOCKED:
        return f"{name} : encore ouvert par aria2, laissé sur place."
    return f"{name} : déplacement impossible."


def _too_soon(db: Session, moment: datetime) -> bool:
    last = _read_stamp(db)
    return last is not None and moment - last < timedelta(hours=1)


def _read_stamp(db: Session) -> datetime | None:
    row = db.get(Setting, _STAMP)
    if row is None or not row.value:
        return None
    try:
        last = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last


def _remember(db: Session, moment: datetime, message: str) -> None:
    payload = json.dumps({"at": moment.isoformat(), "message": message}, ensure_ascii=False)
    row = db.get(Setting, _STAMP)
    if row is None:
        db.add(Setting(key=_STAMP, value=moment.isoformat(), is_secret=False, updated_at=moment))
    else:
        row.value = moment.isoformat()
        row.updated_at = moment
    status = db.get(Setting, _STATUS)
    if status is None:
        db.add(Setting(key=_STATUS, value=payload, is_secret=False, updated_at=moment))
    else:
        status.value = payload
        status.updated_at = moment


def _read_status(db: Session) -> tuple[datetime, str] | None:
    row = db.get(Setting, _STATUS)
    if row is None or not row.value:
        return None
    try:
        body = json.loads(row.value)
        at = datetime.fromisoformat(str(body["at"]))
        message = str(body["message"])
    except (ValueError, KeyError, TypeError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return at, message


def _library_index(movies: Path, series: Path, skip: tuple[Path, ...] = ()) -> set[tuple]:
    found: set[tuple] = set()
    for root, kind in ((movies, "film"), (series, "series")):
        if not root.is_dir() or is_under(root, skip):
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.name.startswith(".") or not is_video(path):
                continue
            if is_under(path, skip) or ".trash" in path.parts or "incomplete" in path.parts:
                continue
            parsed = parse_release(path.name)
            key = show_key(parsed.title) or show_key(path.parent.name)
            if not key:
                continue
            if kind == "series" and (parsed.season is None or parsed.episode is None):
                continue
            slot = (kind, key, parsed.season, parsed.episode) if kind == "series" else ("film", key, None, None)
            found.add(slot)
    return found

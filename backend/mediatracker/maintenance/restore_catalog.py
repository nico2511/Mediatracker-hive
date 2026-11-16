"""List and inspect on-disk SQLite backups under the config directory."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

from mediatracker.config import Settings
from mediatracker.db.migrate import make_config
from mediatracker.maintenance.backup import sqlite_path

_FILENAME = re.compile(
    r"^mediatracker-(?P<date>\d{8})-(?:(?P<tag>reset)-)?(?P<time>\d{6})\.db$",
)
_SAFETY_FILENAME = re.compile(
    r"^mediatracker-(?P<date>\d{8})-before-restore-(?P<time>\d{6})\.db$",
)
_IMPORT_FILENAME = re.compile(
    r"^import-(?P<date>\d{8})-(?P<time>\d{6})(?:-(?P<suffix>\d+))?\.db$",
)
_APP_VERSION_KEY = "system.app_version"


class BackupCatalogError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class BackupSummary:
    name: str
    created_at: datetime
    size_bytes: int


@dataclass(frozen=True)
class BackupPreview:
    name: str
    created_at: datetime
    app_version: str | None
    schema_revision: str | None
    fiches: int
    fiches_delta: int
    restorable: bool
    schema_relation: str
    imported: bool


def backups_root(settings: Settings) -> Path:
    return Path(settings.config_dir) / "backups"


def safety_root(settings: Settings) -> Path:
    return backups_root(settings) / "safety"


def resolve_backup_file(settings: Settings, name: str, *, safety: bool = False) -> Path:
    """Return a backup path inside the backups or safety directory, or raise."""
    if not name or name != Path(name).name:
        raise BackupCatalogError("Nom de sauvegarde invalide.")
    if ".." in Path(name).parts or "/" in name or "\\" in name:
        raise BackupCatalogError("Nom de sauvegarde invalide.")
    root = (safety_root(settings) if safety else backups_root(settings)).resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise BackupCatalogError("Chemin de sauvegarde refusé.")
    if not path.is_file() or not _filename_allowed(path.name, safety=safety):
        raise BackupCatalogError("Sauvegarde introuvable.")
    return path


def list_backups(settings: Settings) -> list[BackupSummary]:
    root = backups_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    rows: list[BackupSummary] = []
    names: set[str] = set()
    for pattern in ("mediatracker-*.db", "import-*.db"):
        for path in root.glob(pattern):
            if not path.is_file() or path.name in names:
                continue
            if not _filename_allowed(path.name, safety=False):
                continue
            names.add(path.name)
            created = _created_at(path)
            rows.append(BackupSummary(name=path.name, created_at=created, size_bytes=path.stat().st_size))
    rows.sort(key=lambda item: item.created_at, reverse=True)
    return rows


def list_safety_backups(settings: Settings) -> list[BackupSummary]:
    root = safety_root(settings)
    root.mkdir(parents=True, exist_ok=True)
    rows: list[BackupSummary] = []
    for path in sorted(root.glob("mediatracker-*.db"), key=lambda item: item.name, reverse=True):
        if not path.is_file() or not _filename_allowed(path.name, safety=True):
            continue
        created = _created_at(path)
        rows.append(BackupSummary(name=path.name, created_at=created, size_bytes=path.stat().st_size))
    return rows


def preview_backup(
    settings: Settings,
    name: str,
    *,
    current_fiches: int,
    safety: bool = False,
) -> BackupPreview:
    path = resolve_backup_file(settings, name, safety=safety)
    meta = _read_backup_meta(path)
    fiches = meta["fiches"]
    revision = meta["schema_revision"]
    relation = schema_relation(revision)
    return BackupPreview(
        name=path.name,
        created_at=_created_at(path),
        app_version=meta["app_version"],
        schema_revision=revision,
        fiches=fiches,
        fiches_delta=fiches - current_fiches,
        restorable=relation != "newer",
        schema_relation=relation,
        imported=is_import_backup_name(path.name),
    )


def is_import_backup_name(name: str) -> bool:
    return _IMPORT_FILENAME.fullmatch(name) is not None


def allocate_import_filename(dest_dir: Path, moment: datetime) -> str:
    """Nom catalogue unique pour un import (suffixe -2, -3… si collision)."""
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    base = f"import-{stamp}.db"
    if not (dest_dir / base).exists():
        return base
    suffix = 2
    while True:
        candidate = f"import-{stamp}-{suffix}.db"
        if not (dest_dir / candidate).exists():
            return candidate
        suffix += 1


def count_fiches(database_url: str) -> int:
    path = sqlite_path(database_url)
    if path is None or not path.is_file():
        return 0
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return _fiche_count(conn)
    finally:
        conn.close()


def schema_relation(backup_revision: str | None) -> str:
    """Return ``same``, ``older``, ``newer``, or ``unknown`` compared with the app migration head."""
    script = ScriptDirectory.from_config(make_config())
    try:
        head = script.get_current_head()
    except CommandError:
        return "unknown"
    if not head or not backup_revision:
        return "unknown"
    if backup_revision == head:
        return "same"
    try:
        script.get_revision(backup_revision)
    except CommandError:
        return "newer"
    for rev in script.iterate_revisions(head, backup_revision):
        if rev.revision == backup_revision:
            return "older"
    return "newer"


def _filename_allowed(name: str, *, safety: bool) -> bool:
    if safety:
        return _SAFETY_FILENAME.fullmatch(name) is not None
    return _FILENAME.fullmatch(name) is not None or _IMPORT_FILENAME.fullmatch(name) is not None


def _read_backup_meta(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        app_version = None
        try:
            row = conn.execute(
                "select value from setting where key = ?",
                (_APP_VERSION_KEY,),
            ).fetchone()
            if row and row[0]:
                app_version = str(row[0])
        except sqlite3.Error:
            app_version = None
        schema_revision = None
        try:
            row = conn.execute("select version_num from alembic_version").fetchone()
            if row and row[0]:
                schema_revision = str(row[0])
        except sqlite3.Error:
            schema_revision = None
        return {
            "app_version": app_version,
            "schema_revision": schema_revision,
            "fiches": _fiche_count(conn),
        }
    finally:
        conn.close()


def _fiche_count(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute(
            "select count(*) from media where type in ('film', 'series')",
        ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0


def _created_at(path: Path) -> datetime:
    match = (
        _FILENAME.fullmatch(path.name)
        or _SAFETY_FILENAME.fullmatch(path.name)
        or _IMPORT_FILENAME.fullmatch(path.name)
    )
    if match:
        stamp = f"{match.group('date')}{match.group('time')}"
        try:
            parsed = datetime.strptime(stamp, "%Y%m%d%H%M%S")
            return parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    moment = path.stat().st_mtime
    return datetime.fromtimestamp(moment, tz=timezone.utc)

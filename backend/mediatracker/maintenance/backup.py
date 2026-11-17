"""Daily online SQLite backups under the config directory, with rotation."""

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.maintenance.backup_hive_identity import (
    BackupHiveIdentityIncompleteError,
    embed_hive_identity_in_backup,
)
from mediatracker.maintenance.backup_sqlite import SQLITE_BACKUP_BUSY_MS

logger = logging.getLogger(__name__)


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:"):
        return None
    url = make_url(database_url)
    if not url.database or url.database == ":memory:":
        return None
    return Path(url.database)


def backup_sqlite(
    source: Path,
    dest_dir: Path,
    keep: int,
    now: datetime | None = None,
    *,
    settings: Settings | None = None,
) -> Path | None:
    """Backup ``source`` via SQLite if that calendar day has no backup yet. Keep the newest ``keep`` files."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    if not source.is_file():
        return None
    moment = now or datetime.now(timezone.utc)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = moment.strftime("%Y%m%d")
    if any(dest_dir.glob(f"mediatracker-{stamp}-*.db")):
        _rotate(dest_dir, keep)
        return None
    dest = dest_dir / f"mediatracker-{stamp}-{moment.strftime('%H%M%S')}.db"
    try:
        _sqlite_backup(source, dest)
        if settings is not None:
            embed_hive_identity_in_backup(dest, settings)
        _secure_backup_file_mode(dest)
    except BackupHiveIdentityIncompleteError:
        dest.unlink(missing_ok=True)
        raise
    _rotate(dest_dir, keep)
    return dest


def backup_sqlite_force(
    source: Path,
    dest_dir: Path,
    now: datetime | None = None,
    *,
    settings: Settings | None = None,
) -> Path:
    """Always write a backup, including when today's daily backup already exists."""
    if not source.is_file():
        raise FileNotFoundError(source)
    moment = now or datetime.now(timezone.utc)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"mediatracker-{moment.strftime('%Y%m%d')}-reset-{moment.strftime('%H%M%S')}.db"
    try:
        _sqlite_backup(source, dest)
        if settings is not None:
            embed_hive_identity_in_backup(dest, settings)
        _secure_backup_file_mode(dest)
    except BackupHiveIdentityIncompleteError:
        dest.unlink(missing_ok=True)
        raise
    return dest


def maybe_backup(settings: Settings, db: Session | None = None, now: datetime | None = None) -> Path | None:
    source = sqlite_path(settings.database_url)
    if source is None:
        return None
    dest = Path(settings.config_dir) / "backups"
    try:
        return backup_sqlite(source, dest, _keep(settings, db), now, settings=settings)
    except (OSError, BackupHiveIdentityIncompleteError):
        logger.info("Sauvegarde SQLite impossible.")
        return None


def _secure_backup_file_mode(path: Path) -> None:
    os.chmod(path, 0o600)


# Pages per backup() step — avoids loading the whole database into memory.
_BACKUP_PAGES = 64


def copy_sqlite_file(source: Path, dest: Path) -> None:
    """Copy one SQLite file onto another via the backup API."""
    _sqlite_backup(source, dest)


def restore_sqlite_into_live(backup: Path, live: Path) -> None:
    """Overwrite the live database from a backup file via the backup API (WAL-safe)."""
    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    src.execute(f"pragma busy_timeout={SQLITE_BACKUP_BUSY_MS}")
    try:
        dst = sqlite3.connect(f"file:{live}", uri=True)
        dst.execute(f"pragma busy_timeout={SQLITE_BACKUP_BUSY_MS}")
        try:
            while src.backup(dst, pages=_BACKUP_PAGES):
                pass
            dst.execute("pragma wal_checkpoint(TRUNCATE)")
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def _sqlite_backup(source: Path, dest: Path) -> None:
    """Online consistent backup using sqlite3.Connection.backup (not a raw file copy)."""
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    src.execute(f"pragma busy_timeout={SQLITE_BACKUP_BUSY_MS}")
    try:
        dst = sqlite3.connect(dest)
        dst.execute(f"pragma busy_timeout={SQLITE_BACKUP_BUSY_MS}")
        try:
            while src.backup(dst, pages=_BACKUP_PAGES):
                pass
        finally:
            dst.close()
    finally:
        src.close()


def _rotate(dest_dir: Path, keep: int) -> None:
    files = sorted(dest_dir.glob("mediatracker-*.db"))
    extra = files[: max(0, len(files) - keep)]
    for path in extra:
        path.unlink(missing_ok=True)


def _keep(settings: Settings, db: Session | None) -> int:
    if db is None:
        return max(1, settings.backup_keep)
    from mediatracker.db.models import Setting

    row = db.get(Setting, "backup_keep")
    if row is None or not row.value:
        return max(1, settings.backup_keep)
    try:
        return max(1, int(row.value))
    except ValueError:
        return max(1, settings.backup_keep)

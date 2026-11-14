"""Wipe the application database after a forced SQLite backup."""

from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.quality_profile import ensure_default_profile
from mediatracker.db.models import Base
from mediatracker.maintenance.backup import backup_sqlite_force, sqlite_path


class ResetError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


def reset_database(db: Session, settings: Settings) -> Path:
    """Copy the database, then delete every application row and reseed the default profile."""
    source = sqlite_path(settings.database_url)
    if source is None or not source.is_file():
        raise ResetError("La base n'est pas un fichier SQLite : rien n'a été effacé.")
    try:
        dest = backup_sqlite_force(source, Path(settings.config_dir) / "backups", settings=settings)
    except OSError as exc:
        raise ResetError("Sauvegarde impossible : la base n'a pas été effacée.") from exc
    db.commit()
    for table in reversed(Base.metadata.sorted_tables):
        db.execute(table.delete())
    db.commit()
    db.expunge_all()
    ensure_default_profile(db)
    return dest

"""Apply Alembic migrations. Used by the API lifespan, the s6 init oneshot, and tests."""

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

from mediatracker.db.session import ensure_sqlite_dir

_PLACEHOLDER = "driver://user:pass@localhost/dbname"


def migrations_dir() -> Path:
    return Path(__file__).resolve().parent / "migrations"


def make_config(database_url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(migrations_dir()))
    url = database_url or os.environ.get("DATABASE_URL") or _PLACEHOLDER
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def run_migrations(database_url: str) -> None:
    ensure_sqlite_dir(database_url)
    command.upgrade(make_config(database_url), "head")

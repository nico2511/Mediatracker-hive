import os

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from mediatracker.db.models import Base
from mediatracker.db.session import apply_sqlite_pragmas

config = context.config
# Do not call fileConfig. alembic.ini's formatter is text, and importing
# fileConfig here would bind the stdlib function before the process guard
# can wrap it. The API, the worker, and the tests install the JSON handler.

target_metadata = Base.metadata
_PLACEHOLDER = "driver://user:pass@localhost/dbname"


def _url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if configured and configured != _PLACEHOLDER:
        return configured
    return os.environ.get("DATABASE_URL") or configured or ""


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    if _url().startswith("sqlite"):
        event.listen(connectable, "connect", apply_sqlite_pragmas)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

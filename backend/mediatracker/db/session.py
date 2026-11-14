"""SQLite engine: WAL, busy timeout, foreign keys. Short transactions, two app processes."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from mediatracker.config import Settings

# sqlite3's timeout is seconds; PRAGMA busy_timeout is milliseconds. Both wait
# out a writer (the download worker) instead of failing the request at once.
SQLITE_BUSY_SECONDS = 15
SQLITE_BUSY_MS = SQLITE_BUSY_SECONDS * 1000
HTTP_SQLITE_RETRY_AFTER_SECONDS = 5


def sqlite_path(database_url: str) -> Path | None:
    if not database_url.startswith("sqlite:///"):
        return None
    raw = database_url.removeprefix("sqlite:///")
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def ensure_sqlite_dir(database_url: str) -> None:
    path = sqlite_path(database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def apply_sqlite_pragmas(dbapi_connection, _record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def rollback_if_inactive(db: Session) -> None:
    """Rollback when the DBAPI transaction was invalidated (e.g. flush bloqué avant SAVEPOINT).

    After a failure inside ``begin_nested()``, SQLAlchemy rolls back only the savepoint
    and keeps the outer transaction active — a full rollback here would drop sibling writes.
    """
    if not db.is_active:
        db.rollback()


def is_sqlite_locked(exc: BaseException) -> bool:
    """True when SQLite refused a write because another connection holds the file."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).lower()
        if "database is locked" in text or "database is busy" in text:
            return True
        current = current.__cause__ or current.__context__
    return False


def release_sqlite_before_network(db: Session) -> None:
    """End the SQLite write transaction before slow I/O so other writers are not blocked.

    Never call ``commit`` while a worker ``begin_nested()`` SAVEPOINT is active: that
    closes the transaction and breaks the savepoint context (InvalidRequestError).

    Inside a SAVEPOINT, callers must defer ``flush()`` until after the network phase
    instead of relying on this helper (worker ``commit=False``).
    """
    if db.in_nested_transaction():
        return
    bind = db.get_bind()
    if bind is None or bind.dialect.name != "sqlite":
        return
    if not db.in_transaction():
        return
    commit_retry(db)


def commit_retry(db: Session, *, attempts: int = 3) -> None:
    """Commit, and try again only if the write is still pending after a lock.

    ``busy_timeout`` already waits inside the first attempt. A failed commit
    rolls the session back; committing again with nothing left to flush would
    succeed and drop the update, so that case is raised for the caller to repeat.
    """
    delay = 0.05
    for attempt in range(attempts):
        try:
            db.commit()
            return
        except OperationalError as exc:
            db.rollback()
            pending = bool(db.new or db.dirty)
            if not is_sqlite_locked(exc) or attempt == attempts - 1 or not pending:
                raise
            time.sleep(delay)
            delay *= 2


def apply_with_retry(
    factory: sessionmaker[Session],
    label: str,
    fn,
    *,
    attempts: int = 3,
):
    """Run a write unit in a fresh session; on lock, rollback, new session, bounded retry."""
    import logging
    import time

    logger = logging.getLogger(__name__)
    delay = 0.05
    for attempt in range(attempts):
        db = factory()
        try:
            result = fn(db)
            commit_retry(db)
            return result
        except OperationalError as exc:
            db.rollback()
            if not is_sqlite_locked(exc) or attempt == attempts - 1:
                raise
            logger.warning("%s : base occupée, nouvel essai (%s/%s).", label, attempt + 2, attempts)
            time.sleep(delay)
            delay *= 2
        finally:
            db.close()
    return None


def create_session_factory(settings: Settings) -> tuple[Engine, sessionmaker[Session]]:
    ensure_sqlite_dir(settings.database_url)
    engine = create_engine(
        settings.database_url,
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": SQLITE_BUSY_SECONDS},
    )
    if settings.database_url.startswith("sqlite"):
        event.listen(engine, "connect", apply_sqlite_pragmas)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return engine, factory


def bind_session_factory(db: Session) -> sessionmaker[Session]:
    """Session factory sharing the bind of an existing session (orchestrated polls from workers)."""
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    session = factory()
    try:
        yield session
    finally:
        session.close()

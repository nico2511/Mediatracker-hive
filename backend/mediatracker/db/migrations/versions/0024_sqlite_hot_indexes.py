"""Indexes for worker polls, events, and the download queue (idempotent).

Revision ID: 0024_sqlite_hot_indexes
Revises: 0023_download_attempt_started
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_sqlite_hot_indexes"
down_revision: str | None = "0023_download_attempt_started"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_event_ts", "event", ("ts",)),
    ("ix_event_type", "event", ("type",)),
    ("ix_event_media_id", "event", ("media_id",)),
    ("ix_download_state", "download", ("state",)),
    ("ix_media_next_source_poll_at", "media", ("next_source_poll_at",)),
    ("ix_download_state_next_retry", "download", ("state", "next_retry_at")),
)


def _has_index(name: str) -> bool:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = :name"),
        {"name": name},
    ).fetchone()
    return row is not None


def _ensure_index(name: str, table: str, columns: tuple[str, ...]) -> None:
    if _has_index(name):
        return
    op.create_index(name, table, list(columns))


def upgrade() -> None:
    for name, table, columns in _INDEXES:
        _ensure_index(name, table, columns)


def downgrade() -> None:
    for name, _table, _columns in reversed(_INDEXES):
        if _has_index(name):
            op.drop_index(name)

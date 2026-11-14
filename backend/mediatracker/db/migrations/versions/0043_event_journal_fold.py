"""Journal fold counters and unchanged poll backoff streak."""

import sqlalchemy as sa
from alembic import op

revision = "0043_event_journal_fold"
down_revision = "0042_catalog_poster_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event",
        sa.Column("repeat_count", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column("event", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "media",
        sa.Column("poll_unchanged_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("media", "poll_unchanged_streak")
    op.drop_column("event", "last_seen_at")
    op.drop_column("event", "repeat_count")

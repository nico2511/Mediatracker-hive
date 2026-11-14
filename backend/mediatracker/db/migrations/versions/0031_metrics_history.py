"""Five-minute metrics history table.

Revision ID: 0031_metrics_history
Revises: 0030_link_dead_memory
Create Date: 2026-10-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_metrics_history"
down_revision: str | None = "0030_link_dead_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metrics_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("bucket_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cpu_api", sa.Float(), nullable=True),
        sa.Column("cpu_worker", sa.Float(), nullable=True),
        sa.Column("rss_api", sa.BigInteger(), nullable=True),
        sa.Column("rss_worker", sa.BigInteger(), nullable=True),
        sa.Column("worker_wakes", sa.JSON(), nullable=False),
        sa.Column("source_requests", sa.JSON(), nullable=False),
        sa.Column("task_durations", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_metrics_history_bucket_at", "metrics_history", ["bucket_at"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_metrics_history_bucket_at", table_name="metrics_history")
    op.drop_table("metrics_history")

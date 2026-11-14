"""Episode traces (voluntary delete, unexpected absence).

Revision ID: 0025_media_item_traces
Revises: 0024_sqlite_hot_indexes
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_media_item_traces"
down_revision: str | None = "0024_sqlite_hot_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "media_item",
        sa.Column("traces", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("media_item", "traces")

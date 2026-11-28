"""task_run.media_id et download.metro_sequence_id (liens r7).

Revision ID: 0089_task_run_media_sequence
Revises: 0088_task_run
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0089_task_run_media_sequence"
down_revision: str | None = "0088_task_run"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("task_run", schema=None) as batch:
        batch.add_column(sa.Column("media_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_task_run_media_id",
            "media",
            ["media_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_task_run_media_id", ["media_id"], unique=False)
    with op.batch_alter_table("download", schema=None) as batch:
        batch.add_column(sa.Column("metro_sequence_id", sa.String(length=36), nullable=True))
        batch.create_index("ix_download_metro_sequence_id", ["metro_sequence_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("download", schema=None) as batch:
        batch.drop_index("ix_download_metro_sequence_id")
        batch.drop_column("metro_sequence_id")
    with op.batch_alter_table("task_run", schema=None) as batch:
        batch.drop_index("ix_task_run_media_id")
        batch.drop_constraint("fk_task_run_media_id", type_="foreignkey")
        batch.drop_column("media_id")

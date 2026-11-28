"""Table task_run (collecte carte réseau).

Revision ID: 0088_task_run
Revises: 0087_media_hidden_at
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0088_task_run"
down_revision: str | None = "0087_media_hidden_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "task_run",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("line", sa.String(length=32), nullable=False),
        sa.Column("station", sa.String(length=8), nullable=False),
        sa.Column("sequence_id", sa.String(length=36), nullable=True),
        sa.Column("parent_run_id", sa.Integer(), nullable=True),
        sa.Column("edge_id", sa.String(length=8), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("cause", sa.Text(), nullable=True),
        sa.Column("subject_ref", sa.String(length=64), nullable=True),
        sa.Column("blocked_by", sa.String(length=64), nullable=True),
        sa.Column("waiting_on", sa.String(length=16), nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["parent_run_id"], ["task_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_run_started_at", "task_run", ["started_at"], unique=False)
    op.create_index("ix_task_run_station_started", "task_run", ["station", "started_at"], unique=False)
    op.create_index("ix_task_run_line_started", "task_run", ["line", "started_at"], unique=False)
    op.create_index("ix_task_run_sequence_id", "task_run", ["sequence_id"], unique=False)
    op.create_index("ix_task_run_subject_ref", "task_run", ["subject_ref"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_task_run_subject_ref", table_name="task_run")
    op.drop_index("ix_task_run_sequence_id", table_name="task_run")
    op.drop_index("ix_task_run_line_started", table_name="task_run")
    op.drop_index("ix_task_run_station_started", table_name="task_run")
    op.drop_index("ix_task_run_started_at", table_name="task_run")
    op.drop_table("task_run")
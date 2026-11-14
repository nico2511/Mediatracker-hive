"""Hive T5 : file d'attente de transfert et quota hebdo réception.

Revision ID: 0054_hive_t5_transfer
Revises: 0053_hive_catalogue_store
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0054_hive_t5_transfer"
down_revision: str | None = "0053_hive_catalogue_store"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "hive_ruche",
        sa.Column("weekly_quota_bytes", sa.BigInteger(), nullable=True),
    )
    op.create_table(
        "hive_transfer_job",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("sha256_hex", sa.String(length=64), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("active_source_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("part_path", sa.Text(), nullable=False),
        sa.Column("bytes_done", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("chunk_digests_json", sa.Text(), nullable=True),
        sa.Column("fiche_json", sa.Text(), nullable=False),
        sa.Column("cle_json", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.Column("episode", sa.Integer(), nullable=True),
        sa.Column("paused_reason", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_hive_transfer_job"),
    )
    op.create_index("ix_hive_transfer_job_state", "hive_transfer_job", ["state"])
    op.create_table(
        "hive_receive_quota",
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("member_instance_id", sa.String(length=48), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("bytes_used", sa.BigInteger(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_receive_quota_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("ruche_id", "member_instance_id", "week_start", name="pk_hive_receive_quota"),
    )


def downgrade() -> None:
    op.drop_table("hive_receive_quota")
    op.drop_index("ix_hive_transfer_job_state", table_name="hive_transfer_job")
    op.drop_table("hive_transfer_job")
    op.drop_column("hive_ruche", "weekly_quota_bytes")

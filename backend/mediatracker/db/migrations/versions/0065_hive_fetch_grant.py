"""Table hive_fetch_grant (jeton d'accès fichier temporaire, LOT1).

Revision ID: 0065_hive_fetch_grant
Revises: 0064_hive_chunk_digests_serve
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_hive_fetch_grant"
down_revision: str | None = "0064_hive_chunk_digests_serve"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hive_fetch_grant",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("grant_sha256_hex", sa.String(length=64), nullable=False),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("file_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("requester_instance_id", sa.String(length=48), nullable=False),
        sa.Column(
            "media_item_id",
            sa.Integer(),
            sa.ForeignKey("media_item.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content_size", sa.BigInteger(), nullable=False),
        sa.Column("content_mtime_ns", sa.BigInteger(), nullable=False),
        sa.Column("content_sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["ruche_id"],
            ["hive_ruche.ruche_id"],
            name="fk_hive_fetch_grant_ruche",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_fetch_grant"),
        sa.UniqueConstraint("grant_sha256_hex", name="uq_hive_fetch_grant_hash"),
    )
    op.create_index(
        "ix_hive_fetch_grant_ruche_requester_exp",
        "hive_fetch_grant",
        ["ruche_id", "requester_instance_id", "expires_at"],
    )
    op.create_index("ix_hive_fetch_grant_expires_at", "hive_fetch_grant", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_hive_fetch_grant_expires_at", table_name="hive_fetch_grant")
    op.drop_index("ix_hive_fetch_grant_ruche_requester_exp", table_name="hive_fetch_grant")
    op.drop_table("hive_fetch_grant")

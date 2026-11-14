"""Contexte Hive fetch-grant côté récepteur (Download + aria2, LOT2).

Revision ID: 0066_download_hive_fetch
Revises: 0065_hive_fetch_grant
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_download_hive_fetch"
down_revision: str | None = "0065_hive_fetch_grant"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_hive_fetch",
        sa.Column("download_id", sa.Integer(), nullable=False),
        sa.Column("sha256_hex", sa.String(length=64), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("active_source_index", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("file_id", sa.LargeBinary(length=16), nullable=False),
        sa.Column("origin_instance_id", sa.String(length=48), nullable=False),
        sa.Column("peer_base_url", sa.Text(), nullable=False),
        sa.Column("expected_size", sa.BigInteger(), nullable=False),
        sa.Column("content_mtime_ns", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("content_sha256_hex", sa.String(length=64), nullable=True),
        sa.Column("grant_ciphertext", sa.Text(), nullable=True),
        sa.Column("grant_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["download_id"],
            ["download.id"],
            ondelete="CASCADE",
            name="fk_download_hive_fetch_download",
        ),
        sa.PrimaryKeyConstraint("download_id", name="pk_download_hive_fetch"),
    )
    op.create_index("ix_download_hive_fetch_sha", "download_hive_fetch", ["sha256_hex"])
    op.execute(
        sa.text(
            "INSERT INTO setting (key, value, is_secret, updated_at) "
            "SELECT 'hive_download_via_fetch_grant', 'false', 0, datetime('now') "
            "WHERE NOT EXISTS (SELECT 1 FROM setting WHERE key = 'hive_download_via_fetch_grant')"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_download_hive_fetch_sha", table_name="download_hive_fetch")
    op.drop_table("download_hive_fetch")
    op.execute(sa.text("DELETE FROM setting WHERE key = 'hive_download_via_fetch_grant'"))

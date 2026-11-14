"""Hive : empreintes morceaux 16 Mio persistées pour GET /hive/files.

Revision ID: 0064_hive_chunk_digests_serve
Revises: 0063_hive_fingerprint_queue
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_hive_chunk_digests_serve"
down_revision: str | None = "0063_hive_fingerprint_queue"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hive_file_fingerprint", sa.Column("chunk_digests_json", sa.Text(), nullable=True))
    op.add_column(
        "hive_file_fingerprint",
        sa.Column("chunk_digest_offset", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "hive_file_fingerprint",
        sa.Column("chunk_digest_partial_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "hive_file_fingerprint",
        sa.Column("chunk_digest_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "hive_file_fingerprint",
        sa.Column("chunk_digests_sha256_hex", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hive_file_fingerprint", "chunk_digests_sha256_hex")
    op.drop_column("hive_file_fingerprint", "chunk_digest_claimed_at")
    op.drop_column("hive_file_fingerprint", "chunk_digest_partial_json")
    op.drop_column("hive_file_fingerprint", "chunk_digest_offset")
    op.drop_column("hive_file_fingerprint", "chunk_digests_json")

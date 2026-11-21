"""Download : compteur de tours de sources vides.

Revision ID: 0086_download_source_rounds
Revises: 0085_hive_auto_take_wanted
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0086_download_source_rounds"
down_revision: str | None = "0085_hive_auto_take_wanted"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "download",
        sa.Column("source_rounds", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("download", "source_rounds")
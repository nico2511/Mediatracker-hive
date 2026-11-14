"""Per-fiche download source order. Null keeps the global order.

Revision ID: 0012_source_order
Revises: 0011_external_ids
Create Date: 2026-10-12

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_source_order"
down_revision: str | None = "0011_external_ids"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("source_order", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "source_order")

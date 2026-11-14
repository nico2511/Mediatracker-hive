"""Last one-click repair summary on a fiche.

Revision ID: 0029_media_last_repair
Revises: 0028_source_binding_path_manual
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029_media_last_repair"
down_revision: str | None = "0028_source_binding_path_manual"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("media", sa.Column("last_repair_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("media", sa.Column("last_repair_summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("media", "last_repair_summary")
    op.drop_column("media", "last_repair_at")

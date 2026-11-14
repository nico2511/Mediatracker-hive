"""Remember when and why a hoster link was marked dead (F8).

Revision ID: 0030_link_dead_memory
Revises: 0029_media_last_repair
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030_link_dead_memory"
down_revision: str | None = "0029_media_last_repair"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("link", sa.Column("dead_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("link", sa.Column("dead_code", sa.String(length=64), nullable=True))
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE link SET dead_at = discovered_at WHERE dead = 1 AND dead_at IS NULL"
        )
    )


def downgrade() -> None:
    op.drop_column("link", "dead_code")
    op.drop_column("link", "dead_at")

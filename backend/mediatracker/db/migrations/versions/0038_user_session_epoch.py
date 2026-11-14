"""Compteur d'invalidation des sessions navigateur (S3g).

Revision ID: 0038_user_session_epoch
Revises: 0037_media_item_still_url
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_user_session_epoch"
down_revision: str | None = "0037_media_item_still_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("session_epoch", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("user", "session_epoch")

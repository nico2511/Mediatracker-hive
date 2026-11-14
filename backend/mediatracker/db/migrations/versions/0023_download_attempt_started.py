"""Remember when the current download attempt started, so a 0 % job can time out.

Revision ID: 0023_download_attempt_started
Revises: 0022_download_forced
Create Date: 2026-10-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_download_attempt_started"
down_revision: str | None = "0022_download_forced"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("download", sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("download", "attempt_started_at")

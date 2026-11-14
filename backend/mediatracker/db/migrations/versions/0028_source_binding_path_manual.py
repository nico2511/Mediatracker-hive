"""Remember when a Wawacity page path was set by a person.

Revision ID: 0028_source_binding_path_manual
Revises: 0027_source_binding_path
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_source_binding_path_manual"
down_revision: str | None = "0027_source_binding_path"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "source_binding",
        sa.Column("path_manual", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("source_binding", "path_manual")

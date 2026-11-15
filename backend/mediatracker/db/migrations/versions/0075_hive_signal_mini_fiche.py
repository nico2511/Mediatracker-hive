"""Hive signal : mini-fiche cherche (affichage sans TMDB).

Revision ID: 0075_hive_signal_mini_fiche
Revises: 0074_hive_wanted_volunteer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0075_hive_signal_mini_fiche"
down_revision: str | None = "0074_hive_wanted_volunteer"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hive_signal", sa.Column("mini_fiche_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("hive_signal", "mini_fiche_json")
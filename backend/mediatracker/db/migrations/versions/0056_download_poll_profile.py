"""Download : identité profil qualité issue du sondage (Lot E).

Revision ID: 0056_download_poll_profile
Revises: 0055_hive_measured_bps

Note: pas de FK Alembic (SQLite) ; suppression bloquée par l'API tant qu'un Download
référence le profil (RESTRICT dans le modèle SQLAlchemy pour les moteurs qui l'appliquent).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0056_download_poll_profile"
down_revision: str | None = "0055_hive_measured_bps"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("download", sa.Column("poll_quality_profile_id", sa.Integer(), nullable=True))
    op.create_index("ix_download_poll_quality_profile_id", "download", ["poll_quality_profile_id"])


def downgrade() -> None:
    op.drop_index("ix_download_poll_quality_profile_id", table_name="download")
    op.drop_column("download", "poll_quality_profile_id")

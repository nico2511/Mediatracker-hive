"""Périmètres de partage Hive (fiche / saison) pour hériter l'intention sur nouveaux fichiers.

Revision ID: 0079_hive_share_scope
Revises: 0078_media_tmdb_trailer
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0079_hive_share_scope"
down_revision: str | None = "0078_media_tmdb_trailer"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "hive_share_scope",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("media_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media.id"],
            name="fk_hive_share_scope_media",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_share_scope"),
    )
    op.create_index("ix_hive_share_scope_media", "hive_share_scope", ["media_id"])
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_hive_share_scope_media_season "
            "ON hive_share_scope (media_id, COALESCE(season, -1))"
        )
    )

    op.create_table(
        "hive_share_scope_exclusion",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scope_id", sa.Integer(), nullable=False),
        sa.Column("ruche_id", sa.LargeBinary(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["scope_id"],
            ["hive_share_scope.id"],
            name="fk_hive_share_scope_exclusion_scope",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hive_share_scope_exclusion"),
        sa.UniqueConstraint("scope_id", "ruche_id", name="uq_hive_share_scope_exclusion_scope_ruche"),
    )
    op.create_index("ix_hive_share_scope_exclusion_scope", "hive_share_scope_exclusion", ["scope_id"])


def downgrade() -> None:
    op.drop_index("ix_hive_share_scope_exclusion_scope", table_name="hive_share_scope_exclusion")
    op.drop_table("hive_share_scope_exclusion")
    op.drop_index("uq_hive_share_scope_media_season", table_name="hive_share_scope")
    op.drop_index("ix_hive_share_scope_media", table_name="hive_share_scope")
    op.drop_table("hive_share_scope")
"""Store Wawacity page path on source_binding for domain-stable URLs.

Revision ID: 0027_source_binding_path
Revises: 0026_remote_search_at
Create Date: 2026-10-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_source_binding_path"
down_revision: str | None = "0026_remote_search_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _path_from_url(url: str | None) -> str | None:
    from urllib.parse import urlparse

    text = (url or "").strip()
    if not text:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        path = parsed.path or "/"
        if parsed.query:
            return f"{path}?{parsed.query}"
        return path
    if text.startswith("/"):
        return text
    if text.startswith("?"):
        return text
    return f"/{text}" if text else None


def upgrade() -> None:
    op.add_column("source_binding", sa.Column("path", sa.Text(), nullable=True))
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, url FROM source_binding WHERE path IS NULL AND url IS NOT NULL")).all()
    for row_id, url in rows:
        path = _path_from_url(url)
        if path:
            connection.execute(
                sa.text("UPDATE source_binding SET path = :path WHERE id = :id AND path IS NULL"),
                {"path": path, "id": row_id},
            )


def downgrade() -> None:
    op.drop_column("source_binding", "path")

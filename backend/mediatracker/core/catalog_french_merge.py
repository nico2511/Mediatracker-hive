"""One-time merge of catalog rows whose title still ends with « - FRENCH »."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.catalog import _absorb, dedupe_key, fold_title
from mediatracker.db.models import CatalogEntry, Setting
from mediatracker.providers.sources.wawacity_html import parse_heading

_MIGRATION_KEY = "catalog_french_title_merged_v1"
_FRENCH_TAIL = re.compile(r"(?i)\s*[-–—|]\s*french\s*$")


def _canonical_title(title: str) -> str | None:
    if not _FRENCH_TAIL.search(title or ""):
        return None
    cleaned = parse_heading(title).title.strip()
    return cleaned or None


def migrate_catalog_french_duplicate_titles(db: Session) -> int:
    """Merge or re-key catalog rows split by a « - FRENCH » suffix (idempotent)."""
    flag = db.get(Setting, _MIGRATION_KEY)
    if flag is not None and flag.value == "true":
        return 0

    touched = 0
    pending_keepers: dict[str, CatalogEntry] = {}
    rows = list(db.scalars(select(CatalogEntry)).all())
    for row in rows:
        canonical = _canonical_title(row.title)
        if not canonical or canonical == row.title:
            continue
        new_key = dedupe_key(canonical, row.year, row.media_type, row.season, row.episode)
        if new_key == row.dedupe_key:
            continue
        keeper = db.scalar(select(CatalogEntry).where(CatalogEntry.dedupe_key == new_key))
        if keeper is None:
            keeper = pending_keepers.get(new_key)
        if keeper is not None and keeper is not row:
            _absorb(keeper, row)
            db.delete(row)
            db.flush()
            touched += 1
            continue
        row.title = canonical
        row.dedupe_key = new_key
        row.title_folded = fold_title(canonical)[:300]
        pending_keepers[new_key] = row
        db.flush()
        touched += 1

    now = datetime.now(timezone.utc)
    if flag is None:
        db.add(Setting(key=_MIGRATION_KEY, value="true", is_secret=False, updated_at=now))
    else:
        flag.value = "true"
        flag.updated_at = now
    return touched


def run_catalog_french_migration_safe(db: Session) -> int:
    """Run the FRENCH merge inside a savepoint; never raise to callers."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        with db.begin_nested():
            return migrate_catalog_french_duplicate_titles(db)
    except Exception:
        logger.exception("Migration catalogue FRENCH ignorée (démarrage poursuivi).")
        return 0

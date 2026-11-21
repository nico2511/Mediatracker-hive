"""Journal et notifications locales quand un quota a été remis à zéro chez une source."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from mediatracker.events import record_event

_TYPE_NOTICE = "hive.quota.reset.notice"


def emit_my_quota_reset_notices(
    db: Session,
    *,
    sources: list[dict],
    when: datetime,
) -> None:
    for row in sources:
        if not isinstance(row, dict):
            continue
        reset_at = row.get("owner_reset_at")
        if not isinstance(reset_at, str) or not reset_at.strip():
            continue
        name = row.get("source_display_name")
        label = name.strip() if isinstance(name, str) and name.strip() else "cette source"
        source_id = row.get("source_instance_id")
        dedupe = None
        if isinstance(source_id, str) and source_id.strip():
            dedupe = f"hive-quota-reset:{source_id}:{reset_at.strip()}"
        record_event(
            db,
            _TYPE_NOTICE,
            level="info",
            payload={
                "message": f"Votre quota chez {label} a été remis à zéro",
                "source_display_name": label,
                "owner_reset_at": reset_at.strip(),
                "source_instance_id": source_id if isinstance(source_id, str) else None,
            },
            dedupe_key=dedupe,
            when=when,
        )
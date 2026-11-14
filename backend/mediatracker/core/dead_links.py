"""Dead hoster link memory (F8): selective revival and weekly AllDebrid re-check."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.pipeline.constants import _DEAD_LINK, host_down
from mediatracker.core.selection import BLOCKED_SOURCE_HOSTERS
from mediatracker.db.models import Link, Setting
from mediatracker.providers.debrid.alldebrid import RETRYABLE_CODES, AllDebridError

logger = logging.getLogger(__name__)

REVIVE_AFTER = timedelta(days=7)
RECHECK_INTERVAL = timedelta(days=7)
RECHECK_KEY = "alldebrid.dead_links_rechecked_at"
BATCH_SIZE = 25

# Transient outages: safe to retry soon. Permanent file loss waits out REVIVE_AFTER.
TRANSIENT_DEAD_CODES = frozenset(RETRYABLE_CODES) | frozenset(
    {
        "RATE_LIMIT",
        "ARIA2",
        "STALLED",
        "LINK_HOST_UNAVAILABLE",
        "NO_SERVER",
    }
)
PERMANENT_DEAD_CODES = frozenset(_DEAD_LINK) | frozenset({"DISCONTINUED", "MAGNET_INVALID"})


def mark_link_dead(link: Link, code: str | None, message: str | None, now: datetime) -> None:
    moment = _aware(now)
    link.dead = True
    link.dead_at = moment
    link.dead_code = (code or "").strip()[:64] or None
    if message:
        link.last_error = message[:500]


def clear_dead_state(link: Link) -> None:
    link.dead = False
    link.dead_at = None
    link.dead_code = None
    link.last_error = None


def eligible_for_retry_revival(link: Link, now: datetime) -> bool:
    """Whether a manual retry may try this link again."""
    if not link.dead:
        return True
    if (link.hoster or "").casefold() in BLOCKED_SOURCE_HOSTERS:
        return False
    if _transient_dead(link):
        return True
    dead_at = _aware(link.dead_at) if link.dead_at is not None else None
    if dead_at is None:
        return False
    return _aware(now) - dead_at >= REVIVE_AFTER


def revive_links_for_retry(db: Session, item_id: int, now: datetime) -> int:
    revived = 0
    for link in db.scalars(select(Link).where(Link.media_item_id == item_id)).all():
        if not eligible_for_retry_revival(link, now):
            continue
        clear_dead_state(link)
        revived += 1
    return revived


def maybe_reverify_dead_links(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    debrid=None,
) -> bool:
    """Batch ``/v4/link/infos`` for dead hoster links, at most once per week."""
    moment = _aware(now or datetime.now(timezone.utc))
    if not _recheck_due(db, moment):
        return False
    from mediatracker.core.pipeline.clients import _debrid

    _row, client = _debrid(db, settings, debrid)
    if client is None or not hasattr(client, "link_infos"):
        _schedule_recheck(db, moment)
        return False
    rows = list(
        db.scalars(
            select(Link).where(Link.dead.is_(True), Link.kind == "ddl_hoster")
        ).all()
    )
    urls = [row.url for row in rows if (row.hoster or "").casefold() not in BLOCKED_SOURCE_HOSTERS]
    if not urls:
        _schedule_recheck(db, moment)
        return False
    by_url = {row.url: row for row in rows}
    revived = 0
    for offset in range(0, len(urls), BATCH_SIZE):
        chunk = urls[offset : offset + BATCH_SIZE]
        try:
            infos = client.link_infos(chunk)
        except AllDebridError as exc:
            logger.info("Revérification des liens morts ignorée (%s).", exc.code)
            _schedule_recheck(db, moment + timedelta(hours=6))
            return False
        for info in infos:
            row = by_url.get(info.link)
            if row is None:
                continue
            if info.online:
                clear_dead_state(row)
                revived += 1
            elif info.code:
                mark_link_dead(row, info.code, info.message, moment)
    if revived:
        logger.info("Revérification AllDebrid : %s lien(s) de nouveau en ligne.", revived)
    _schedule_recheck(db, moment)
    return revived > 0


def _recheck_due(db: Session, now: datetime) -> bool:
    last = _parse_setting_time(db, RECHECK_KEY)
    if last is None:
        return True
    return now - last >= RECHECK_INTERVAL


def _schedule_recheck(db: Session, when: datetime) -> None:
    row = db.get(Setting, RECHECK_KEY)
    if row is None:
        db.add(Setting(key=RECHECK_KEY, value=when.isoformat()))
    else:
        row.value = when.isoformat()


def _transient_dead(link: Link) -> bool:
    code = (link.dead_code or "").strip()
    if code in TRANSIENT_DEAD_CODES:
        return True
    if code in PERMANENT_DEAD_CODES:
        return False
    if host_down(code, link.last_error):
        return True
    text = (link.last_error or "").casefold()
    if "hors ligne" in text or "lien mort" in text:
        return False
    if "indisponible" in text or "trop de requêtes" in text:
        return True
    return False


def _parse_setting_time(db: Session, key: str) -> datetime | None:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return _aware(parsed)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

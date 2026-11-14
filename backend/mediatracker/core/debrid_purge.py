"""Drop AllDebrid entries once MediaTracker has filed the download.

Saved links are removed one batch at a time (``/v4/user/links/delete``).
History entries for those same hoster URLs go through ``/v4/user/history/delete``
with ``links[]``. The bare call, which wipes the whole recent history, is only
used when the person confirms « tout purger ». Magnets use ``/v4/magnet/delete``.

Calls are spaced by at least one second. One summary line is logged; URLs are not.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Download, Setting
from mediatracker.providers.debrid.alldebrid import AllDebridError

logger = logging.getLogger(__name__)

PURGE_GAP_SECONDS = 1.0
CHUNK = 20
RETRY_KEY = "alldebrid.purge_retry_at"
RETRY_AFTER = timedelta(minutes=30)


@dataclass(frozen=True)
class PurgeReport:
    saved: int = 0
    history: int = 0
    magnets: int = 0
    history_wiped: bool = False
    error: str | None = None

    @property
    def empty(self) -> bool:
        return self.saved == 0 and self.history == 0 and self.magnets == 0 and not self.history_wiped


class PurgePacer:
    """At least ``delay`` seconds between purge requests. The first call does not wait."""

    def __init__(
        self,
        delay: float = PURGE_GAP_SECONDS,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.delay = max(0.0, delay)
        self._clock = clock
        self._sleeper = sleeper
        self._next = 0.0
        self.waits: list[float] = []

    def wait(self) -> None:
        now = self._clock()
        gap = max(0.0, self._next - now)
        self._next = max(self._next, now) + self.delay
        self.waits.append(gap)
        if gap:
            self._sleeper(gap)


def purge_account(
    client,
    *,
    saved: list[str],
    history: list[str] | None,
    magnets: list[str],
    wipe_history: bool = False,
    pacer: PurgePacer | None = None,
) -> PurgeReport:
    """Delete the given entries. ``wipe_history`` sends history/delete with no link list."""
    pace = pacer or PurgePacer()
    saved_urls = _unique(saved)
    history_urls = [] if wipe_history else _unique(history or [])
    magnet_ids = _unique(magnets)
    saved_n = history_n = magnet_n = 0
    wiped = False
    error: str | None = None
    try:
        for batch in _chunks(saved_urls, CHUNK):
            pace.wait()
            client.delete_saved_links(batch)
            saved_n += len(batch)
        if wipe_history:
            pace.wait()
            client.delete_history(None)
            wiped = True
        else:
            for batch in _chunks(history_urls, CHUNK):
                pace.wait()
                client.delete_history(batch)
                history_n += len(batch)
        for magnet_id in magnet_ids:
            pace.wait()
            client.delete_magnet(magnet_id)
            magnet_n += 1
    except AllDebridError as exc:
        error = exc.code
    report = PurgeReport(
        saved=saved_n,
        history=history_n,
        magnets=magnet_n,
        history_wiped=wiped,
        error=error,
    )
    logger.info("%s", summary(report))
    return report


def summary(report: PurgeReport) -> str:
    """One French line. It never includes a URL."""
    if report.history_wiped:
        history = "historique récent vidé"
    else:
        history = f"{report.history} entrée(s) d'historique"
    body = f"{report.saved} lien(s) enregistré(s), {history}, {report.magnets} aimant(s)."
    if report.error and report.empty:
        return f"Purge AllDebrid impossible ({report.error})."
    if report.error:
        return f"Purge AllDebrid incomplète ({report.error}). {body}"
    if report.empty:
        return "Rien à purger sur AllDebrid."
    return f"AllDebrid purgé : {body}"


def maybe_auto_purge(
    db: Session,
    settings: Settings,
    debrid,
    now: datetime,
    *,
    pacer: PurgePacer | None = None,
) -> PurgeReport | None:
    """Purge finished MediaTracker downloads when the setting is on. One attempt, then a pause."""
    if not _enabled(db, settings):
        return None
    if not _can_purge(debrid):
        return None
    if _retry_later(db, now):
        return None
    rows = _pending(db)
    if not rows:
        return None
    saved = [row.debrid_link for row in rows if row.debrid_link]
    history = [row.debrid_link for row in rows if row.debrid_link]
    magnets = [row.debrid_magnet_id for row in rows if row.debrid_magnet_id]
    row_ids = [row.id for row in rows]
    db.commit()
    report = purge_account(
        debrid,
        saved=saved,
        history=history,
        magnets=magnets,
        pacer=pacer,
    )
    if report.error:
        _remember_retry(db, now + RETRY_AFTER)
        return report
    for row_id in row_ids:
        row = db.get(Download, row_id)
        if row is not None:
            row.debrid_purged_at = now
    _clear_retry(db)
    from mediatracker.core.precheck import clear_known_files_cache

    clear_known_files_cache()
    return report


def targets_for_scope(
    db: Session,
    client,
    *,
    scope: str,
    pacer: PurgePacer | None = None,
) -> tuple[list[str], list[str] | None, list[str], bool]:
    """Return saved URLs, history URLs, magnet ids, and whether history is wiped whole."""
    pace = pacer or PurgePacer(0)
    if scope == "all":
        pace.wait()
        saved = [row.link for row in client.saved_links()]
        pace.wait()
        magnets = client.list_magnet_ids()
        return saved, None, magnets, True
    rows = _owned(db)
    links = _unique(row.debrid_link for row in rows if row.debrid_link)
    pace.wait()
    present = {item.link for item in client.saved_links()}
    saved = [link for link in links if link in present] if present else links
    magnets = _unique(row.debrid_magnet_id for row in rows if row.debrid_magnet_id)
    return saved, links, magnets, False


def mark_purged(db: Session, now: datetime) -> None:
    for row in _owned(db):
        row.debrid_purged_at = now


def _owned(db: Session) -> list[Download]:
    return list(
        db.scalars(
            select(Download).where(
                Download.state == "done",
                or_(Download.debrid_link.is_not(None), Download.debrid_magnet_id.is_not(None)),
            )
        ).all()
    )


def _pending(db: Session) -> list[Download]:
    return [row for row in _owned(db) if row.debrid_purged_at is None]


def _can_purge(debrid) -> bool:
    return debrid is not None and hasattr(debrid, "delete_saved_links") and hasattr(debrid, "delete_history")


def _enabled(db: Session, settings: Settings) -> bool:
    row = db.get(Setting, "alldebrid_auto_purge")
    if row is None or row.value is None or row.value == "":
        return settings.alldebrid_auto_purge
    return row.value == "true"


def _retry_later(db: Session, now: datetime) -> bool:
    row = db.get(Setting, RETRY_KEY)
    if row is None or not row.value:
        return False
    try:
        moment = datetime.fromisoformat(row.value)
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment > now


def _remember_retry(db: Session, when: datetime) -> None:
    row = db.get(Setting, RETRY_KEY)
    if row is None:
        db.add(Setting(key=RETRY_KEY, value=when.isoformat(), is_secret=False, updated_at=when))
        return
    row.value = when.isoformat()
    row.updated_at = when


def _clear_retry(db: Session) -> None:
    row = db.get(Setting, RETRY_KEY)
    if row is not None:
        row.value = ""


def _unique(values) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        found.append(value)
    return found


def _chunks(values: list[str], size: int):
    for start in range(0, len(values), size):
        yield values[start : start + size]

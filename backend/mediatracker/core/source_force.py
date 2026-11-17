"""Ask the worker to check every followed source now, one media per tick.

The HTTP action only records the request. The worker calls ``pump_forced_source_check``,
which runs the same poll as a manual check (``force=True``) and keeps the existing
delay between source requests. Nothing here calls AllDebrid.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
from mediatracker.core.source_poll import run_poll  # noqa: F401 — point de patch tests / compat
from mediatracker.db.models import Media, Provider, Setting
from mediatracker.providers.sources.loading import enabled_remote_sources, wawacity_config  # noqa: F401

FORCE_KEY = "system.force_source_check_at"


def source_configured(db: Session, settings: Settings) -> bool:
    row = db.scalar(
        select(Provider).where(
            Provider.kind == "source",
            Provider.type == "wawacity",
            Provider.enabled.is_(True),
        )
    )
    if row is None:
        return False
    return bool(wawacity_config(db, settings).base_url)


def request_source_check(db: Session, now: datetime | None = None) -> int:
    """Mark a wave. Returns how many followed fiches the worker will visit."""
    moment = now or datetime.now(timezone.utc)
    _put(db, FORCE_KEY, moment.isoformat(), moment)
    from mediatracker.config import get_settings
    from mediatracker.core.worker_wake import REASON_API_HA, wake_worker

    wake_worker(db, get_settings(), REASON_API_HA, commit=False)
    db.commit()
    return _pending_count(db, moment)


def pump_forced_source_check(db: Session, settings: Settings, *, commit: bool = True) -> bool:
    """Poll one fiche of the current wave. True when this tick was consumed."""
    since = _since(db)
    if since is None:
        return False
    media = _next_media(db, since)
    if media is None:
        _put(db, FORCE_KEY, "", datetime.now(timezone.utc))
        if commit:
            db.commit()
        else:
            db.flush()
        return False
    from mediatracker.db.session import bind_session_factory

    factory = bind_session_factory(db)
    orchestrate_profile_first_poll(factory, settings, media.id, force=True)
    media = db.get(Media, media.id)
    if media is not None:
        _advance_past_wave(db, media, since, settings, commit=commit)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def _pending_count(db: Session, since: datetime) -> int:
    return len(_candidates(db, since))


def _next_media(db: Session, since: datetime) -> Media | None:
    rows = _candidates(db, since)
    if not rows:
        return None
    rows.sort(key=lambda row: (_aware(row.last_source_poll_at) or datetime.min.replace(tzinfo=timezone.utc), row.id))
    return rows[0]


def _candidates(db: Session, since: datetime) -> list[Media]:
    rows = db.scalars(
        select(Media).where(
            Media.followed.is_(True),
            Media.on_hold.is_(False),
            Media.new_episode_policy != "off",
        )
    ).all()
    pending: list[Media] = []
    for row in rows:
        last = _aware(row.last_source_poll_at)
        if last is None or last < since:
            pending.append(row)
    return pending


def _advance_past_wave(db: Session, media: Media, since: datetime, settings: Settings, *, commit: bool = True) -> None:
    """One fiche per wave. A poll that does not move ``last_source_poll_at`` must not run again next tick."""
    from mediatracker.providers.sources.loading import _int_setting

    now = datetime.now(timezone.utc)
    last = _aware(media.last_source_poll_at)
    if last is None or last < since:
        media.last_source_poll_at = since + timedelta(microseconds=1)
    nxt = _aware(media.next_source_poll_at)
    if nxt is None or nxt <= now:
        interval_default = _int_setting(db, "scan_interval_min", settings.scan_interval_min)
        minutes = media.scan_interval_min or interval_default
        media.next_source_poll_at = now + timedelta(minutes=max(minutes, 60))
    if commit:
        db.commit()
    else:
        db.flush()


def _since(db: Session) -> datetime | None:
    row = db.get(Setting, FORCE_KEY)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    return _aware(parsed)


def _put(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

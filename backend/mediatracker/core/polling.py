"""When a followed film or series may be polled on a source.

Polling starts on the air or release date, then respects the interval
(default 12 h) and the post-air window. It does not start early.
A followed series on automatic download is also due, on that same interval,
while an unwatched episode with a past TMDB date is still missing. Propose
and pause stay on the window.
The poller waits ``source_request_delay_seconds`` between HTTP calls and, on
error, sets ``next_source_poll_at`` from the exponential backoff. This module
only decides eligibility. It never performs a request.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

TERMINAL_STATUSES = frozenset({"ended", "canceled"})


@dataclass(frozen=True)
class EpisodeAir:
    air_date: date | None
    downloaded: bool
    on_disk: bool = False
    watched: bool = False
    season: int | None = None
    voluntarily_removed: bool = False


@dataclass(frozen=True)
class PollSubject:
    followed: bool
    on_hold: bool
    media_type: str
    series_status: str | None
    new_episode_policy: str
    last_source_poll_at: datetime | None
    next_source_poll_at: datetime | None
    next_air_date: date | None
    episodes: tuple[EpisodeAir, ...] = ()
    catch_up: bool = False
    backlog: bool = False
    # None follows every season. An empty tuple follows none.
    tracked_seasons: tuple[int, ...] | None = None


def should_poll_source(
    subject: PollSubject,
    *,
    now: datetime,
    interval: timedelta,
    ongoing_only: bool,
    window_days: int,
) -> tuple[bool, str]:
    """Return (allowed, reason). Reasons are stable codes for tests and logs."""
    if subject.media_type not in {"series", "film"}:
        return False, "not_series"
    if not subject.followed:
        return False, "not_followed"
    if subject.on_hold:
        return False, "on_hold"
    if subject.new_episode_policy == "off":
        return False, "policy_off"
    if subject.media_type == "film" and _film_obtained(subject):
        return False, "film_obtained"
    nxt = _utc(subject.next_source_poll_at)
    if nxt is not None and now < nxt:
        return False, "backoff"
    if subject.backlog:
        return True, "backlog"
    if subject.catch_up and subject.new_episode_policy == "auto":
        return True, "catch_up"
    today = now.date()
    gap = _has_past_gap(subject, today)
    if (
        not gap
        and subject.media_type == "series"
        and ongoing_only
        and (subject.series_status or "") in TERMINAL_STATUSES
    ):
        return False, "series_finished"
    last = _utc(subject.last_source_poll_at)
    if last is not None and now - last < interval:
        return False, "interval"
    if gap:
        return True, "missing_episode"
    if subject.media_type == "film":
        return _should_poll_film(subject, today=today)
    known = _known_air_dates(subject)
    if not known:
        # No TMDB date yet: the slow interval is the only throttle.
        return True, "no_air_date"
    if all(air > today for air in known):
        return False, "before_air_date"
    if any(air <= today and (today - air).days <= window_days for air in known):
        return True, "air_window"
    return False, "outside_air_window"


def _should_poll_film(subject: PollSubject, *, today: date) -> tuple[bool, str]:
    """Films: start at availability date, then keep trying with interval/backoff (no air window)."""
    if _film_obtained(subject):
        return False, "film_obtained"
    when = subject.next_air_date
    if when is not None and when > today:
        return False, "before_air_date"
    if when is None:
        if not _film_unseen_missing(subject):
            return False, "outside_air_window"
        return True, "no_air_date"
    if _film_unseen_missing(subject):
        return True, "film_missing"
    return False, "outside_air_window"


def _film_unseen_missing(subject: PollSubject) -> bool:
    if not subject.episodes:
        return True
    for episode in subject.episodes:
        if episode.watched:
            return False
        if episode.downloaded or episode.on_disk or episode.voluntarily_removed:
            return False
    return True


def _film_obtained(subject: PollSubject) -> bool:
    """A film is one-shot: once the file is here, polling stops."""
    return any(episode.downloaded or episode.on_disk for episode in subject.episodes)


def _utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive timestamps. Treat them as UTC before comparing."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _has_past_gap(subject: PollSubject, today: date) -> bool:
    """Unwatched episode already aired, with no file, on a followed auto series."""
    if subject.media_type != "series" or subject.new_episode_policy != "auto":
        return False
    if not subject.followed or subject.on_hold:
        return False
    chosen = subject.tracked_seasons
    for episode in subject.episodes:
        if episode.watched or episode.downloaded or episode.on_disk or episode.voluntarily_removed:
            continue
        if episode.air_date is None or episode.air_date > today:
            continue
        if chosen is not None and episode.season not in chosen:
            continue
        return True
    return False


def _known_air_dates(subject: PollSubject) -> list[date]:
    dates: list[date] = []
    if subject.next_air_date is not None:
        dates.append(subject.next_air_date)
    for episode in subject.episodes:
        if episode.downloaded or episode.air_date is None:
            continue
        dates.append(episode.air_date)
    return dates

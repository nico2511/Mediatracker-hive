"""Home « À trier » alerts: renewed unfollowed series, unwatched on-disk seasons, follow fatigue."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.display_title import clean_display_title
from mediatracker.core.file_status import FileStatusIndex
from mediatracker.core.media_hide import media_visible_clause
from mediatracker.db.models import Download, Event, Media, MediaItem, MediaSeason, Setting

TO_SORT_IGNORED_KEY = "home_to_sort_ignored"
_IDLE_DAYS = 183
_MIN_PENDING = 3
_STALE_FILE_DAYS = 30
_RENEWED_RECENT_DAYS = 60
_FINISHED_SERIES_STATUSES = frozenset({"ended", "canceled"})
_IGNORE_KEY_RE = re.compile(r"^(renewed:\d+:\d+|unwatched:\d+:\d+|stop_follow:\d+)$")
_FR_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

ReasonKind = Literal["renewed", "unwatched_season", "stop_follow"]


@dataclass(frozen=True)
class ToSortHighlight:
    text: str


@dataclass(frozen=True)
class ToSortLine:
    media_id: int
    media_title: str
    poster_url: str | None
    kind: ReasonKind
    detail: str
    highlight: ToSortHighlight | None
    ignore_key: str
    season: int | None = None
    seasons: tuple[int, ...] = ()


@dataclass(frozen=True)
class ToSortGroup:
    kind: ReasonKind
    title: str
    items: tuple[ToSortLine, ...]


@dataclass(frozen=True)
class ToSortSnapshot:
    total: int
    groups: tuple[ToSortGroup, ...]
    footer: str


def validate_ignore_key(key: str) -> bool:
    return bool(_IGNORE_KEY_RE.match(key))


def _media_ids_from_ignore_keys(keys: set[str]) -> set[int]:
    media_ids: set[int] = set()
    for key in keys:
        if not validate_ignore_key(key):
            continue
        kind, mid, *_rest = key.split(":")
        if kind in {"renewed", "unwatched", "stop_follow"}:
            media_ids.add(int(mid))
    return media_ids


def _purge_payload(db: Session, payload: dict[str, bool]) -> dict[str, bool]:
    active = {key for key, on in payload.items() if on and validate_ignore_key(key)}
    if not active:
        return {}
    existing = set(
        db.scalars(select(Media.id).where(Media.id.in_(_media_ids_from_ignore_keys(active)))).all()
    )
    return {key: True for key in active if int(key.split(":")[1]) in existing}


def load_ignored(db: Session) -> set[str]:
    row = db.get(Setting, TO_SORT_IGNORED_KEY)
    if row is None or not row.value:
        return set()
    try:
        payload = json.loads(row.value)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, dict):
        return set()
    purged = _purge_payload(db, payload)
    if purged != {k: v for k, v in payload.items() if v}:
        when = row.updated_at or datetime.now(timezone.utc)
        encoded = json.dumps(purged, sort_keys=True) if purged else "{}"
        row.value = encoded
        row.updated_at = when
    return set(purged.keys())


def remember_ignore(db: Session, key: str, *, moment: datetime | None = None) -> None:
    if not validate_ignore_key(key):
        raise ValueError("invalid ignore key")
    when = moment or datetime.now(timezone.utc)
    row = db.get(Setting, TO_SORT_IGNORED_KEY)
    payload: dict[str, bool]
    if row is None or not row.value:
        payload = {}
    else:
        try:
            payload = json.loads(row.value)
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
    payload = _purge_payload(db, payload)
    payload[key] = True
    encoded = json.dumps(payload, sort_keys=True)
    if row is None:
        db.add(Setting(key=TO_SORT_IGNORED_KEY, value=encoded, is_secret=False, updated_at=when))
    else:
        row.value = encoded
        row.updated_at = when


def compute_to_sort(db: Session, *, today: date | None = None) -> ToSortSnapshot:
    day = today or date.today()
    ignored = load_ignored(db)
    renewed = _renewed_lines(db, day, ignored)
    unwatched = _unwatched_season_lines(db, day, ignored)
    stop = _stop_follow_lines(db, day, ignored)
    groups: list[ToSortGroup] = []
    if renewed:
        groups.append(ToSortGroup(kind="renewed", title="Renouvelées, non suivies", items=tuple(renewed)))
    if unwatched:
        groups.append(
            ToSortGroup(kind="unwatched_season", title="Saisons jamais regardées", items=tuple(unwatched))
        )
    if stop:
        groups.append(ToSortGroup(kind="stop_follow", title="Arrêter le suivi ?", items=tuple(stop)))
    total = sum(len(group.items) for group in groups)
    footer = (
        "Rien n'est arrêté sans votre accord. Seules les séries terminées et complètes s'arrêtent d'elles-mêmes."
    )
    return ToSortSnapshot(total=total, groups=tuple(groups), footer=footer)


def _shown(value: str | None) -> str:
    return clean_display_title(value) or (value or "").strip()


def _format_fr_date(day: date) -> str:
    month = _FR_MONTHS[day.month - 1]
    return f"{day.day} {month} {day.year}"


def _months_idle(last: datetime, today: date) -> int:
    ref = last.date() if last.tzinfo else last.replace(tzinfo=timezone.utc).date()
    days = (today - ref).days
    return max(1, days // 30)


def _was_followed_before_ids(db: Session, media_ids: list[int]) -> set[int]:
    if not media_ids:
        return set()
    unfollowed = set(
        db.scalars(
            select(Event.media_id).where(
                Event.type == "media.unfollowed",
                Event.media_id.in_(media_ids),
            )
        ).all()
    )
    watched = set(
        db.scalars(
            select(MediaItem.media_id).where(
                MediaItem.media_id.in_(media_ids),
                MediaItem.watched.is_(True),
            )
        ).all()
    )
    return {mid for mid in unfollowed if mid is not None} | {mid for mid in watched if mid is not None}


def _renewed_detail(season_number: int, announce_day: date, today: date, was_followed: bool) -> tuple[str, str]:
    when = _format_fr_date(announce_day)
    if announce_day >= today:
        detail = f"Saison {season_number} annoncée pour le {when}."
    else:
        detail = f"Saison {season_number} en cours (depuis le {when})."
    if was_followed:
        detail += " Vous ne la suivez plus."
    return detail, when


def _renewed_lines(db: Session, today: date, ignored: set[str]) -> list[ToSortLine]:
    media_rows = list(
        db.scalars(
            select(Media)
            .where(Media.type == "series", Media.followed.is_(False), media_visible_clause())
            .order_by(Media.title, Media.id)
        ).all()
    )
    if not media_rows:
        return []
    media_ids = [row.id for row in media_rows]
    was_followed = _was_followed_before_ids(db, media_ids)
    seasons = list(
        db.scalars(select(MediaSeason).where(MediaSeason.media_id.in_(media_ids)).order_by(MediaSeason.number)).all()
    )
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id.in_(media_ids))).all())
    file_status = FileStatusIndex.load(db, items)
    by_media: dict[int, list[MediaSeason]] = defaultdict(list)
    for season in seasons:
        by_media[season.media_id].append(season)
    episode_by_media: dict[int, list[MediaItem]] = defaultdict(list)
    for item in items:
        episode_by_media[item.media_id].append(item)

    lines: list[ToSortLine] = []
    for media in media_rows:
        media_episodes = episode_by_media.get(media.id, [])
        announced = _announced_season(
            by_media.get(media.id, []),
            media_episodes,
            today,
            ignored=ignored,
            media_id=media.id,
            highest_user_season=_highest_user_season(media_episodes, file_status),
            series_status=media.series_status,
        )
        if announced is None:
            continue
        season_number, announce_day = announced
        ignore_key = f"renewed:{media.id}:{season_number}"
        detail, when = _renewed_detail(
            season_number, announce_day, today, media.id in was_followed
        )
        lines.append(
            ToSortLine(
                media_id=media.id,
                media_title=_shown(media.title),
                poster_url=media.poster_url,
                kind="renewed",
                detail=detail,
                highlight=ToSortHighlight(text=when),
                season=season_number,
                ignore_key=ignore_key,
            )
        )
    return lines


def _highest_user_season(episodes: list[MediaItem], status: FileStatusIndex) -> int:
    highest = 0
    for item in episodes:
        if item.season is None or item.season <= 0:
            continue
        season_number = int(item.season)
        if item.watched or status.for_item(item) == "present":
            highest = max(highest, season_number)
    return highest


def _season_episodes(episodes: list[MediaItem], season_number: int) -> list[MediaItem]:
    return [item for item in episodes if item.season == season_number and item.episode is not None]


def _season_has_upcoming(season: MediaSeason, season_eps: list[MediaItem], today: date) -> bool:
    if season.air_date is not None and season.air_date >= today:
        return True
    return any(item.air_date is not None and item.air_date >= today for item in season_eps)


def _season_started_day(season: MediaSeason, season_eps: list[MediaItem]) -> date | None:
    if season.air_date is not None:
        return season.air_date
    aired = [item.air_date for item in season_eps if item.air_date is not None]
    return min(aired) if aired else None


def _season_renewal_reference_day(season: MediaSeason, season_eps: list[MediaItem], today: date) -> date | None:
    future = [item.air_date for item in season_eps if item.air_date is not None and item.air_date >= today]
    day = season.air_date
    if day is None:
        if not future:
            return None
        return min(future)
    if day < today and future:
        return min(future)
    return day


def _season_is_renewal_candidate(season: MediaSeason, season_eps: list[MediaItem], today: date) -> bool:
    if _season_has_upcoming(season, season_eps, today):
        return True
    started = _season_started_day(season, season_eps)
    if started is None or started >= today:
        return False
    recent_cutoff = today - timedelta(days=_RENEWED_RECENT_DAYS)
    return started >= recent_cutoff


def _announced_season(
    seasons: list[MediaSeason],
    episodes: list[MediaItem],
    today: date,
    *,
    ignored: set[str],
    media_id: int,
    highest_user_season: int,
    series_status: str | None,
) -> tuple[int, date] | None:
    if highest_user_season <= 0:
        return None
    series_finished = (series_status or "").lower() in _FINISHED_SERIES_STATUSES
    candidates: list[tuple[int, date]] = []
    for season in seasons:
        if season.number is None or season.number <= 0:
            continue
        if season.number <= highest_user_season:
            continue
        if f"renewed:{media_id}:{season.number}" in ignored:
            continue
        season_eps = _season_episodes(episodes, season.number)
        if not _season_is_renewal_candidate(season, season_eps, today):
            continue
        ref_day = _season_renewal_reference_day(season, season_eps, today)
        if ref_day is None:
            continue
        candidates.append((season.number, ref_day))
    if not candidates:
        return None
    future = [pair for pair in candidates if pair[1] >= today]
    if series_finished:
        if not future:
            return None
        future.sort(key=lambda pair: (pair[1], pair[0]))
        return future[0]
    if future:
        future.sort(key=lambda pair: (pair[1], pair[0]))
        return future[0]
    best = max(candidates, key=lambda pair: (pair[0], pair[1]))
    return best


def _as_utc_day(moment: datetime) -> date:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).date()


_EPOCH_DAY = date(1970, 1, 1)


def _last_file_day(present: list[MediaItem], arrivals: dict[int, datetime]) -> date:
    days: list[date] = []
    for item in present:
        if item.id is None:
            days.append(_EPOCH_DAY)
            continue
        moment = item.downloaded_at or arrivals.get(item.id)
        days.append(_as_utc_day(moment) if moment is not None else _EPOCH_DAY)
    if not days:
        return _EPOCH_DAY
    return max(days)


def _unwatched_season_lines(db: Session, today: date, ignored: set[str]) -> list[ToSortLine]:
    media_rows = list(
        db.scalars(
            select(Media).where(Media.type == "series", media_visible_clause()).order_by(Media.title, Media.id)
        ).all()
    )
    if not media_rows:
        return []
    media_ids = [row.id for row in media_rows]
    items = list(
        db.scalars(
            select(MediaItem).where(
                MediaItem.media_id.in_(media_ids),
                MediaItem.season.is_not(None),
                MediaItem.episode.is_not(None),
                MediaItem.season > 0,
            )
        ).all()
    )
    if not items:
        return []
    status = FileStatusIndex.load(db, items)
    item_ids = [item.id for item in items if item.id is not None]
    arrivals: dict[int, datetime] = {}
    if item_ids:
        rows = db.execute(
            select(Download.media_item_id, Download.finished_at).where(
                Download.media_item_id.in_(item_ids),
                Download.finished_at.is_not(None),
            )
        ).all()
        for media_item_id, finished_at in rows:
            if media_item_id is None or finished_at is None:
                continue
            prev = arrivals.get(media_item_id)
            if prev is None or finished_at > prev:
                arrivals[media_item_id] = finished_at
    stale_cutoff = today - timedelta(days=_STALE_FILE_DAYS)
    by_media: dict[int, dict[int, list[MediaItem]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        by_media[item.media_id][int(item.season)].append(item)

    lines: list[ToSortLine] = []
    for media in media_rows:
        for season_number in sorted(by_media.get(media.id, {})):
            ignore_key = f"unwatched:{media.id}:{season_number}"
            if ignore_key in ignored:
                continue
            bucket = by_media[media.id][season_number]
            if any(item.watched for item in bucket):
                continue
            present = [item for item in bucket if status.for_item(item) == "present"]
            if not present:
                continue
            if _last_file_day(present, arrivals) > stale_cutoff:
                continue
            aired = [item for item in bucket if item.air_date is None or item.air_date <= today]
            if aired and not all(status.for_item(item) == "present" for item in aired):
                continue
            label = f"Saison {season_number}"
            if aired and all(status.for_item(item) == "present" for item in aired):
                detail = f"{label} complète sur le disque, aucun épisode vu."
            else:
                detail = f"{label} sur le disque, aucun épisode vu."
            lines.append(
                ToSortLine(
                    media_id=media.id,
                    media_title=_shown(media.title),
                    poster_url=media.poster_url,
                    kind="unwatched_season",
                    detail=detail,
                    highlight=None,
                    season=season_number,
                    seasons=(season_number,),
                    ignore_key=ignore_key,
                )
            )
    return lines


def _stop_follow_lines(db: Session, today: date, ignored: set[str]) -> list[ToSortLine]:
    media_rows = list(
        db.scalars(
            select(Media)
            .where(
                Media.type == "series",
                Media.followed.is_(True),
                Media.on_hold.is_(False),
                media_visible_clause(),
            )
            .order_by(Media.title, Media.id)
        ).all()
    )
    if not media_rows:
        return []
    media_ids = [row.id for row in media_rows]
    items = list(
        db.scalars(
            select(MediaItem).where(
                MediaItem.media_id.in_(media_ids),
                MediaItem.season.is_not(None),
                MediaItem.episode.is_not(None),
                MediaItem.season != 0,
            )
        ).all()
    )
    by_media: dict[int, list[MediaItem]] = defaultdict(list)
    for item in items:
        by_media[item.media_id].append(item)
    cutoff = today - timedelta(days=_IDLE_DAYS)
    lines: list[ToSortLine] = []
    for media in media_rows:
        ignore_key = f"stop_follow:{media.id}"
        if ignore_key in ignored:
            continue
        bucket = by_media.get(media.id, [])
        pending = [
            item
            for item in bucket
            if not item.watched and item.air_date is not None and item.air_date <= today and item.episode is not None
        ]
        if len(pending) < _MIN_PENDING:
            continue
        watched_at = [item.watched_at for item in bucket if item.watched and item.watched_at is not None]
        if not watched_at:
            continue
        latest = max(watched_at)
        latest_day = latest.date() if latest.tzinfo else latest.replace(tzinfo=timezone.utc).date()
        if latest_day > cutoff:
            continue
        months = _months_idle(latest, today)
        highlight_text = f"{months} mois"
        lines.append(
            ToSortLine(
                media_id=media.id,
                media_title=_shown(media.title),
                poster_url=media.poster_url,
                kind="stop_follow",
                detail=f"Aucun épisode vu depuis {highlight_text}, {len(pending)} épisodes en attente.",
                highlight=ToSortHighlight(text=highlight_text),
                ignore_key=ignore_key,
            )
        )
    return lines


def snapshot_to_api(snapshot: ToSortSnapshot) -> dict[str, Any]:
    return {
        "total": snapshot.total,
        "footer": snapshot.footer,
        "groups": [
            {
                "kind": group.kind,
                "title": group.title,
                "items": [
                    {
                        "media_id": line.media_id,
                        "media_title": line.media_title,
                        "poster_url": line.poster_url,
                        "kind": line.kind,
                        "detail": line.detail,
                        "highlight": line.highlight.text if line.highlight else None,
                        "season": line.season,
                        "seasons": list(line.seasons),
                        "ignore_key": line.ignore_key,
                    }
                    for line in group.items
                ],
            }
            for group in snapshot.groups
        ],
    }

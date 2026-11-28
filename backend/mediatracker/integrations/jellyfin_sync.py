"""Apply Jellyfin watched state and debounce the library refresh.

The single configured user is the only one synced. Polling is the fallback
when the webhook plugin is not pointed at this API; the default is 30 minutes.
"""

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings, get_settings
from mediatracker.core.metrics.worker_counters import TASK_JELLYFIN_SYNC, track_task
from mediatracker.db.models import Media, MediaItem, Setting
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.events import record_event
from mediatracker.integrations.jellyfin import (
    JellyfinClient,
    JellyfinError,
    JellyfinWatch,
    canonical_user_guid,
    from_api_item,
    parse_webhook,
    paths_match,
    resolve_user_id,
)
from mediatracker.integrations.paths import parse_path_map
from mediatracker.library.parse import normalize_title
from mediatracker.security.crypto import decrypt_secret

logger = logging.getLogger(__name__)

REFRESH_AT = "jellyfin.refresh_at"
LAST_POLL_AT = "jellyfin.last_watched_sync_at"
LAST_ERROR = "jellyfin.last_sync_error"
POLL_RETRY_AT = "jellyfin.poll_retry_at"
POLL_FAIL_COUNT = "jellyfin.poll_fail_count"
DEBOUNCE = timedelta(seconds=60)
MIN_POLL_RETRY = timedelta(minutes=5)


def schedule_library_refresh(db: Session, now: datetime, *, delay: timedelta = DEBOUNCE) -> None:
    """Coalesce a burst of filed downloads into one Jellyfin refresh."""
    target = now + delay
    row = db.get(Setting, REFRESH_AT)
    if row is None:
        db.add(Setting(key=REFRESH_AT, value=target.isoformat(), is_secret=False, updated_at=now))
        _remember(db, "jellyfin.align_pending", "1", now)
        _remember(db, "jellyfin.align_saw_scan", "", now)
        db.flush()
        return
    current = _parse_time(row.value)
    if current is not None and current > now:
        _remember(db, "jellyfin.align_pending", "1", now)
        return
    row.value = target.isoformat()
    row.updated_at = now
    _remember(db, "jellyfin.align_pending", "1", now)
    _remember(db, "jellyfin.align_saw_scan", "", now)


def maybe_sync_jellyfin(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client: JellyfinClient | None = None,
) -> None:
    moment = now or datetime.now(timezone.utc)
    url, api_key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    if not url or not api_key:
        return
    owns = client is None
    if client is None:
        client = JellyfinClient(url, api_key, user_id or None)
    try:
        with track_task(TASK_JELLYFIN_SYNC):
            refresh_fired = False
            refresh_ms: int | None = None
            if _refresh_due(db, moment):
                release_sqlite_before_network(db)
                import time

                t0 = time.perf_counter()
                try:
                    client.refresh_library()
                except JellyfinError as exc:
                    logger.info("Refresh Jellyfin ignoré (%s).", exc.code)
                else:
                    _clear(db, REFRESH_AT, moment)
                    _touch(db, "jellyfin.refresh_fired_at", moment)
                    refresh_fired = True
                    refresh_ms = int((time.perf_counter() - t0) * 1000)
            from mediatracker.metro.instrument import instrument_jellyfin_refresh, instrument_jellyfin_sync
            from mediatracker.metro.record import safe_instrument

            safe_instrument(instrument_jellyfin_refresh, fired=refresh_fired, duration_ms=refresh_ms)
            poll_min = _poll_minutes(db, settings)
            polled = False
            if _poll_due(db, moment, poll_min):
                release_sqlite_before_network(db)
                try:
                    resolved = resolve_configured_user(db, client, user_id, moment)
                    release_sqlite_before_network(db)
                    raw_items = client.user_items(resolved)
                    series_index = client.series_index(resolved) if hasattr(client, "series_index") else {}
                except JellyfinError as exc:
                    logger.info("Synchro Jellyfin ignorée : %s", exc.message)
                    _remember(db, LAST_ERROR, exc.message, moment)
                    _schedule_poll_retry(db, moment, poll_min)
                else:
                    watches = [
                        item
                        for item in (from_api_item(row, series_index) for row in raw_items)
                        if item is not None
                    ]
                    apply_watches(
                        db,
                        watches,
                        prefix or None,
                        moment,
                        user_filter=resolved,
                        path_map=path_map,
                        series_index=series_index,
                    )
                    _touch(db, LAST_POLL_AT, moment)
                    _remember(db, LAST_ERROR, "", moment)
                    _clear_poll_retry(db, moment)
                    polled = True
            safe_instrument(instrument_jellyfin_sync, polled=polled)
    finally:
        if owns:
            client.close()
    db.commit()


def sync_watched_now(db: Session, settings: Settings, *, now: datetime | None = None) -> int:
    """One watched-state pass for the configured user, ignoring the poll interval."""
    moment = now or datetime.now(timezone.utc)
    url, api_key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    if not url or not api_key:
        raise JellyfinError("NOT_CONFIGURED", "Jellyfin n'est pas configuré.")
    client = JellyfinClient(url, api_key, user_id or None)
    try:
        release_sqlite_before_network(db)
        resolved = resolve_configured_user(db, client, user_id, moment)
        release_sqlite_before_network(db)
        raw_items = client.user_items(resolved)
        series_index = client.series_index(resolved) if hasattr(client, "series_index") else {}
        watches = [item for item in (from_api_item(row, series_index) for row in raw_items) if item is not None]
        changed = apply_watches(
            db,
            watches,
            prefix or None,
            moment,
            user_filter=resolved,
            path_map=path_map,
            series_index=series_index,
        )
    except JellyfinError as exc:
        _remember(db, LAST_ERROR, exc.message, moment)
        db.commit()
        raise
    else:
        _touch(db, LAST_POLL_AT, moment)
        _remember(db, LAST_ERROR, "", moment)
        _clear_poll_retry(db, moment)
        db.commit()
        return changed
    finally:
        client.close()


def apply_webhook(
    db: Session,
    settings: Settings,
    payload: dict,
    now: datetime,
    *,
    client: JellyfinClient | None = None,
) -> int:
    watch = parse_webhook(payload)
    if watch is None:
        return 0
    url, api_key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    if user_id and watch.user_id and not _same_jellyfin_user(user_id, watch.user_id):
        return 0
    series_index: dict[str, dict] = {}
    resolved_user = user_id or None
    owns = client is None
    if client is None and url and api_key:
        client = JellyfinClient(url, api_key, user_id or None)
    if client is not None and url and api_key:
        try:
            resolved_user = resolve_configured_user(db, client, user_id, now)
            if watch.series_id:
                release_sqlite_before_network(db)
                series_index = client.series_index(resolved_user)
        except JellyfinError:
            series_index = {}
    try:
        return apply_watches(
            db,
            [watch],
            prefix or None,
            now,
            user_filter=resolved_user,
            path_map=path_map,
            series_index=series_index,
        )
    finally:
        if owns and client is not None:
            client.close()


def apply_watches(
    db: Session,
    watches: list[JellyfinWatch],
    path_prefix: str | None,
    now: datetime,
    *,
    user_filter: str | None = None,
    path_map: str | None = None,
    series_index: dict[str, dict] | None = None,
) -> int:
    changed = 0
    touched_media: set[int] = set()
    medias = list(db.scalars(select(Media)).all())
    accepted = {row.id: accepted_series_ids(row, watches, series_index) for row in medias}
    for watch in watches:
        if user_filter and watch.user_id and not _same_jellyfin_user(user_filter, watch.user_id):
            logger.debug(
                "Événement Jellyfin ignoré : utilisateur %s ne correspond pas au filtre configuré.",
                watch.user_id,
            )
            continue
        for item in match_media_items(db, watch, path_prefix, path_map, accepted):
            _remember_external_ids(db, item.media_id, watch)
            if apply_played(db, item, watch, now):
                changed += 1
                touched_media.add(item.media_id)
    if touched_media:
        from mediatracker.core.fiche_check import touch_fiche_checked
        from mediatracker.core.series_follow import stop_following_complete_series

        for media_id in touched_media:
            row = db.get(Media, media_id)
            touch_fiche_checked(row, now)
            if row is not None and row.type == "series":
                stop_following_complete_series(db, row, today=now.date())
    return changed


def match_media_items(
    db: Session,
    watch: JellyfinWatch,
    path_prefix: str | None,
    path_map: str | None = None,
    series_ids: dict[int, set[str]] | None = None,
) -> list[MediaItem]:
    """Map a Jellyfin item via its id, the file path, then TMDB provider ids."""
    if watch.item_id:
        stored = list(db.scalars(select(MediaItem).where(MediaItem.jellyfin_item_id == watch.item_id)).all())
        if stored:
            return stored
    items = list(db.scalars(select(MediaItem)).all())
    media = {row.id: row for row in db.scalars(select(Media)).all()}
    by_path = [
        item
        for item in items
        if paths_match(item.file_path, watch.path, path_prefix, parse_path_map(path_map))
    ]
    if by_path:
        return _same_file(items, by_path)
    episode_ids = {value for value in (watch.tmdb_id, watch.episode_tmdb_id) if value is not None}
    by_episode = [item for item in items if item.tmdb_episode_id is not None and item.tmdb_episode_id in episode_ids]
    if by_episode:
        return by_episode
    if watch.season is not None and watch.episode is not None:
        slotted = [
            item
            for item in items
            if item.season == watch.season
            and item.episode == watch.episode
            and (row := media.get(item.media_id)) is not None
            and _episode_belongs(row, watch, series_ids)
        ]
        if slotted:
            return slotted
    if watch.item_type == "Movie" or (watch.season is None and watch.episode is None):
        films = [
            item
            for item in items
            if (row := media.get(item.media_id)) is not None
            and row.type == "film"
            and _episode_belongs(row, watch, series_ids)
        ]
        if films:
            return films
    return []


def _same_series(row: Media, watch: JellyfinWatch) -> bool:
    """Linked Jellyfin id, then provider ids, then a loose title and a year within one."""
    manual = (row.jellyfin_series_id or "").strip()
    if manual and watch.series_id == manual:
        return True
    return _identity_matches(
        row,
        watch.series_tmdb_id,
        watch.tvdb_id,
        watch.imdb_id,
        [watch.series_name, watch.series_original_name],
        watch.series_year,
        fallback_tmdb=watch.tmdb_id,
    )


def _remember_external_ids(db: Session, media_id: int, watch: JellyfinWatch) -> None:
    row = db.get(Media, media_id)
    if row is None:
        return
    if row.tvdb_id is None and watch.tvdb_id is not None:
        row.tvdb_id = watch.tvdb_id
    if not row.imdb_id and watch.imdb_id:
        row.imdb_id = watch.imdb_id
    series_tmdb = watch.series_tmdb_id
    if row.tmdb_id is None and series_tmdb is not None and watch.item_type != "Episode":
        row.tmdb_id = series_tmdb


def apply_played(db: Session, item: MediaItem, watch: JellyfinWatch, now: datetime) -> bool:
    changed = False
    if watch.item_id and item.jellyfin_item_id != watch.item_id:
        item.jellyfin_item_id = watch.item_id
        changed = True
    if item.watched == watch.played:
        return changed
    from mediatracker.core.watch_manual_lock import clear_manual_lock, jellyfin_blocks_manual

    activity = watch.last_played_at
    if activity is None and watch.playback_event and watch.played:
        activity = now
    if jellyfin_blocks_manual(item, watch.played, activity):
        return changed
    if item.watch_source == "manual":
        clear_manual_lock(item)
    item.watched = watch.played
    if watch.played:
        item.watched_at = activity or item.watched_at or now
        item.state = "watched"
        from mediatracker.core.cleanup_classify import request_classify

        request_classify(db)
        record_event(
            db,
            "item.watched",
            media_id=item.media_id,
            item_id=item.id,
            payload={"source": "jellyfin"},
        )
    else:
        item.watched_at = None
        if item.state == "watched":
            item.state = "downloaded" if item.downloaded else "missing"
    return True


def diagnose_media(
    db: Session,
    media: Media,
    watches: list[JellyfinWatch],
    synced_at: str | None,
    series_index: dict[str, dict] | None = None,
) -> dict:
    """Why each episode is or is not marked watched from the current Jellyfin items.

    A missing series is one summary. Per-episode misses are returned separately so the
    fiche can show a count and hide the lines until they are opened. Season 0 is
    omitted unless Jellyfin actually has that special.
    """
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    accepted = accepted_series_ids(media, watches, series_index)
    logger.info(
        "Jellyfin média %s : séries %s.",
        media.id,
        ", ".join(sorted(accepted)) if accepted else "aucune",
    )
    names = _series_names(accepted, watches, series_index)
    primary = (media.jellyfin_series_id or "").strip() or (sorted(accepted)[0] if accepted else None)
    if not accepted:
        return {
            "series_id": None,
            "series_ids": [],
            "series_name": None,
            "manual_series_id": (media.jellyfin_series_id or "").strip() or None,
            "synced_at": synced_at,
            "summary": _not_found_summary(media),
            "episodes": [],
            "unmatched": [],
        }
    matched: list[dict] = []
    unmatched: list[dict] = []
    for item in sorted(items, key=lambda row: (row.season or 0, row.episode or 0, row.id)):
        if _skip_special(item, watches, accepted):
            continue
        slot = [
            watch
            for watch in watches
            if watch.item_type == "Episode" and watch.season == item.season and watch.episode == item.episode
        ]
        same = [watch for watch in slot if _episode_belongs(media, watch, {media.id: accepted})]
        if same:
            watch = same[0]
            if watch.played:
                reason = "Vu dans Jellyfin."
            elif watch.played_percent is not None:
                reason = f"Présent dans Jellyfin, lecture {watch.played_percent} %."
            else:
                reason = "Présent dans Jellyfin, non marqué vu."
            matched.append(_diag_row(item, True, watch.played, reason))
            continue
        if slot:
            reason = "L'épisode existe dans Jellyfin mais pas pour cette série."
        else:
            label = f"S{item.season or 0:02d}E{item.episode or 0:02d}" if item.episode is not None else "Film"
            reason = f"{label} absent des épisodes Jellyfin de cette série."
        unmatched.append(_diag_row(item, False, False, reason))
    summary = f"{len(unmatched)} épisode(s) non rapproché(s)." if unmatched else None
    return {
        "series_id": primary,
        "series_ids": sorted(accepted),
        "series_name": names.get(primary or "", next(iter(names.values()), None)),
        "manual_series_id": (media.jellyfin_series_id or "").strip() or None,
        "synced_at": synced_at,
        "summary": summary,
        "episodes": matched,
        "unmatched": unmatched,
    }


def accepted_series_ids(
    media: Media,
    watches: list[JellyfinWatch],
    series_index: dict[str, dict] | None = None,
) -> set[str]:
    """Every Jellyfin series id that belongs to this fiche, not just the first hit."""
    views: dict[str, _SeriesView] = {}
    for series_id, raw in (series_index or {}).items():
        if isinstance(series_id, str) and series_id and isinstance(raw, dict):
            views[series_id] = _view_from_item(raw)
    for watch in watches:
        if not watch.series_id:
            continue
        views[watch.series_id] = _merge_view(views.get(watch.series_id), _view_from_watch(watch))
    accepted: set[str] = set()
    manual = (media.jellyfin_series_id or "").strip()
    if manual:
        accepted.add(manual)
    for series_id, view in views.items():
        if _view_matches(media, view):
            accepted.add(series_id)
    seeds = [views[series_id] for series_id in accepted if series_id in views]
    for series_id, view in views.items():
        if series_id in accepted:
            continue
        if _shares_provider(view, seeds):
            accepted.add(series_id)
    return accepted


def _episode_belongs(row: Media, watch: JellyfinWatch, series_ids: dict[int, set[str]] | None) -> bool:
    manual = (row.jellyfin_series_id or "").strip()
    if manual and watch.series_id == manual:
        return True
    accepted = (series_ids or {}).get(row.id) or set()
    if watch.series_id and watch.series_id in accepted:
        return True
    if watch.series_id and accepted:
        return False
    return _same_series(row, watch)


def _identity_matches(
    row: Media,
    tmdb: int | None,
    tvdb: int | None,
    imdb: str | None,
    names: list[str | None],
    year: int | None,
    fallback_tmdb: int | None = None,
) -> bool:
    """Provider ids win. An episode TMDB only counts when the series id is missing.

    A different series TMDB blocks the title fallback. An episode-only id never does.
    """
    if row.tvdb_id is not None and tvdb is not None and row.tvdb_id != tvdb:
        return False
    if row.imdb_id and imdb and row.imdb_id.casefold() != imdb.casefold():
        return False
    if row.tmdb_id is not None and tmdb is not None and row.tmdb_id != tmdb:
        return False
    if row.tmdb_id is not None and tmdb is not None and row.tmdb_id == tmdb:
        return True
    if row.tmdb_id is not None and tmdb is None and fallback_tmdb is not None and row.tmdb_id == fallback_tmdb:
        return True
    if row.tvdb_id is not None and tvdb is not None and row.tvdb_id == tvdb:
        return True
    if row.imdb_id and imdb and row.imdb_id.casefold() == imdb.casefold():
        return True
    return _titles_close(row, names, year)


def _titles_close(row: Media, names: list[str | None], year: int | None) -> bool:
    wanted = [text for text in (_loose_title(name) for name in (row.title, row.original_title)) if text]
    found = [text for text in (_loose_title(name) for name in names) if text]
    if not wanted or not found or not any(left == right for left in wanted for right in found):
        return False
    if row.year is not None and year is not None and abs(row.year - year) > 1:
        return False
    return True


_ARTICLES = ("the ", "a ", "an ", "le ", "la ", "les ", "l ", "un ", "une ", "des ")


def _loose_title(value: str | None) -> str:
    text = normalize_title(value or "")
    text = re.sub(r"\b(?:19|20)\d{2}\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for article in _ARTICLES:
        if text.startswith(article):
            text = text[len(article) :].strip()
            break
    return text


def _not_found_summary(media: Media) -> str:
    tried: list[str] = []
    if (media.jellyfin_series_id or "").strip():
        tried.append(f"identifiant lié {media.jellyfin_series_id}")
    tried.append(f"TMDB {media.tmdb_id}" if media.tmdb_id is not None else "TMDB")
    tried.append(f"TVDB {media.tvdb_id}" if media.tvdb_id is not None else "TVDB")
    tried.append(f"IMDB {media.imdb_id}" if media.imdb_id else "IMDB")
    tried.append(f"titre « {media.title} »")
    if media.original_title and media.original_title != media.title:
        tried.append(f"titre original « {media.original_title} »")
    tried.append(f"année {media.year} (±1)" if media.year is not None else "année")
    return "Série introuvable dans Jellyfin. Essai : " + ", ".join(tried) + "."


def _skip_special(item: MediaItem, watches: list[JellyfinWatch], accepted: set[str]) -> bool:
    if item.season != 0:
        return False
    return not any(
        watch.item_type == "Episode"
        and watch.season == 0
        and watch.episode == item.episode
        and watch.series_id in accepted
        for watch in watches
    )


def _diag_row(item: MediaItem, matched: bool, played: bool, reason: str) -> dict:
    return {
        "item_id": item.id,
        "season": item.season,
        "episode": item.episode,
        "matched": matched,
        "played": played,
        "reason": reason,
    }


def _series_names(
    accepted: set[str],
    watches: list[JellyfinWatch],
    series_index: dict[str, dict] | None,
) -> dict[str, str]:
    names: dict[str, str] = {}
    for series_id, raw in (series_index or {}).items():
        if series_id in accepted and isinstance(raw, dict) and isinstance(raw.get("Name"), str) and raw["Name"]:
            names[series_id] = raw["Name"]
    for watch in watches:
        if watch.series_id in accepted and watch.series_name and watch.series_id not in names:
            names[watch.series_id] = watch.series_name
    return names


class _SeriesView:
    def __init__(
        self,
        tmdb: int | None = None,
        tvdb: int | None = None,
        imdb: str | None = None,
        name: str | None = None,
        original: str | None = None,
        year: int | None = None,
    ) -> None:
        self.tmdb = tmdb
        self.tvdb = tvdb
        self.imdb = imdb
        self.name = name
        self.original = original
        self.year = year


def _view_from_item(raw: dict) -> _SeriesView:
    providers = raw.get("ProviderIds") if isinstance(raw.get("ProviderIds"), dict) else {}
    return _SeriesView(
        tmdb=_provider_int(providers.get("Tmdb"), providers.get("TMDB")),
        tvdb=_provider_int(providers.get("Tvdb"), providers.get("TVDB")),
        imdb=_provider_text(providers.get("Imdb") or providers.get("IMDB")),
        name=raw.get("Name") if isinstance(raw.get("Name"), str) else None,
        original=raw.get("OriginalTitle") if isinstance(raw.get("OriginalTitle"), str) else None,
        year=raw.get("ProductionYear") if isinstance(raw.get("ProductionYear"), int) else None,
    )


def _view_from_watch(watch: JellyfinWatch) -> _SeriesView:
    return _SeriesView(
        tmdb=watch.series_tmdb_id,
        tvdb=watch.tvdb_id,
        imdb=watch.imdb_id,
        name=watch.series_name,
        original=watch.series_original_name,
        year=watch.series_year,
    )


def _merge_view(left: _SeriesView | None, right: _SeriesView) -> _SeriesView:
    if left is None:
        return right
    return _SeriesView(
        tmdb=left.tmdb if left.tmdb is not None else right.tmdb,
        tvdb=left.tvdb if left.tvdb is not None else right.tvdb,
        imdb=left.imdb or right.imdb,
        name=left.name or right.name,
        original=left.original or right.original,
        year=left.year if left.year is not None else right.year,
    )


def _view_matches(media: Media, view: _SeriesView) -> bool:
    return _identity_matches(media, view.tmdb, view.tvdb, view.imdb, [view.name, view.original], view.year)


def _shares_provider(view: _SeriesView, seeds: list[_SeriesView]) -> bool:
    own = _provider_pairs(view.tmdb, view.tvdb, view.imdb)
    if not own:
        return False
    for seed in seeds:
        if own & _provider_pairs(seed.tmdb, seed.tvdb, seed.imdb):
            return True
    return False


def _provider_pairs(tmdb: int | None, tvdb: int | None, imdb: str | None) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    if tmdb is not None:
        pairs.add(("tmdb", str(tmdb)))
    if tvdb is not None:
        pairs.add(("tvdb", str(tvdb)))
    if imdb:
        pairs.add(("imdb", imdb.casefold()))
    return pairs


def _provider_int(*values: object) -> int | None:
    for value in values:
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def _provider_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _same_jellyfin_user(stored: str, incoming: str) -> bool:
    if stored == incoming:
        return True
    left = canonical_user_guid(stored)
    right = canonical_user_guid(incoming)
    return bool(left and right and left == right)


def jellyfin_settings(db: Session, settings: Settings) -> tuple[str, str, str, str, str]:
    stored_map = _text(db, "jellyfin_path_map")
    return (
        _text(db, "jellyfin_url"),
        _secret(db, "jellyfin_api_key", settings),
        _text(db, "jellyfin_user_id"),
        _text(db, "jellyfin_path_prefix"),
        stored_map or (settings.jellyfin_path_map or ""),
    )


def webhook_secret(db: Session, settings: Settings) -> str:
    return _secret(db, "jellyfin_webhook_secret", settings)


def webhook_token_preview(secret: str) -> str | None:
    if not secret:
        return None
    visible = secret[-4:]
    hidden = len(secret) - len(visible)
    return f"{'•' * hidden}{visible}"


def save_webhook_secret(db: Session, settings: Settings, plain: str, moment: datetime) -> None:
    from mediatracker.security.crypto import encrypt_secret

    row = db.get(Setting, "jellyfin_webhook_secret")
    stored = encrypt_secret(plain, settings.secret_key) if plain else ""
    if row is None:
        db.add(Setting(key="jellyfin_webhook_secret", value=stored, is_secret=True, updated_at=moment))
        return
    row.value = stored
    row.is_secret = True
    row.updated_at = moment


def resolve_configured_user(
    db: Session,
    client: JellyfinClient,
    configured: str | None,
    now: datetime | None = None,
) -> str:
    """Use a stored GUID as-is. A username is resolved once and the GUID is saved."""
    release_sqlite_before_network(db)
    resolved = resolve_user_id(client, configured)
    moment = now or datetime.now(timezone.utc)
    if resolved and resolved != (configured or "").strip():
        _remember(db, "jellyfin_user_id", resolved, moment)
        db.commit()
    return resolved


def _same_file(items: list[MediaItem], hits: list[MediaItem]) -> list[MediaItem]:
    paths = {item.file_path for item in hits if item.file_path}
    if not paths:
        return hits
    siblings = [item for item in items if item.file_path in paths]
    return siblings or hits


def _refresh_due(db: Session, now: datetime) -> bool:
    moment = _parse_time(_text(db, REFRESH_AT))
    return moment is not None and moment <= now


def _poll_due(db: Session, now: datetime, minutes: int) -> bool:
    retry = _parse_time(_text(db, POLL_RETRY_AT))
    if retry is not None and now < retry:
        return False
    last = _parse_time(_text(db, LAST_POLL_AT))
    if last is None:
        return True
    return now - last >= timedelta(minutes=minutes)


def _schedule_poll_retry(db: Session, now: datetime, poll_minutes: int) -> None:
    count = int(_text(db, POLL_FAIL_COUNT) or "0") + 1
    _remember(db, POLL_FAIL_COUNT, str(count), now)
    normal = timedelta(minutes=poll_minutes)
    delay = min(normal, MIN_POLL_RETRY * count)
    _remember(db, POLL_RETRY_AT, (now + delay).isoformat(), now)


def _clear_poll_retry(db: Session, now: datetime) -> None:
    _clear(db, POLL_RETRY_AT, now)
    _remember(db, POLL_FAIL_COUNT, "0", now)


def _poll_minutes(db: Session, settings: Settings | None = None) -> int:
    raw = _text(db, "jellyfin_poll_interval_min")
    if raw:
        try:
            return int(raw)
        except ValueError:
            pass
    if settings is not None:
        return settings.jellyfin_poll_interval_min
    return get_settings().jellyfin_poll_interval_min


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _secret(db: Session, key: str, settings: Settings) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return decrypt_secret(row.value, settings.secret_key)


def _remember(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.updated_at = now


def _touch(db: Session, key: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=now.isoformat(), is_secret=False, updated_at=now))
        return
    row.value = now.isoformat()
    row.updated_at = now


def _clear(db: Session, key: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        return
    row.value = ""
    row.updated_at = now


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed

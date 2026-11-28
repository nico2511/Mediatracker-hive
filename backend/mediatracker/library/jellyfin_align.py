"""Align fiches with Jellyfin, then rename folders from the winning identity.

The newer write wins. A MediaTracker TMDB edit is pushed. A newer Jellyfin
``DateLastSaved`` is adopted. A value this app just pushed is an echo.
Nothing is moved until a rename is validated, unless automatic rename is on.
A file already in ``[tmdbid-A]`` is never filed under a different ``[tmdbid-B]``,
including by automatic rename. A library scan in progress blocks both.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.library_plan import _rollback_paths
from mediatracker.core.naming import atomic_move, different_tmdb_ids, library_destination
from mediatracker.core.storage import is_under, library_skip, storage_layout
from mediatracker.db.models import (
    Download,
    Event,
    JellyfinLibraryItem,
    JellyfinWatchSnapshot,
    LibraryFile,
    LibraryRename,
    Link,
    Media,
    MediaItem,
    Setting,
    SourceBinding,
)
from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, resolve_user_id
from mediatracker.integrations.jellyfin_library import (
    CatalogItem,
    parse_catalog_item,
    read_scan_status,
)
from mediatracker.integrations.jellyfin_sync import _poll_minutes, jellyfin_settings
from mediatracker.integrations.paths import parse_path_map, translate_path, untranslate_path
from mediatracker.library.release_name import retarget_path
from mediatracker.library.scanner import library_roots

logger = logging.getLogger(__name__)

ALIGN_PENDING = "jellyfin.align_pending"
ALIGN_SAW = "jellyfin.align_saw_scan"
REFRESH_FIRED = "jellyfin.refresh_fired_at"
LIBRARY_SYNCED = "jellyfin.library_synced_at"
RESTORE_PENDING = "jellyfin.restore_pending"
RESTORE_SAW = "jellyfin.restore_saw_scan"
RESTORE_AT = "jellyfin.restore_at"
AUTO_RENAME = "jellyfin_auto_rename"
WAIT_FOR_SCAN = timedelta(seconds=20)
RESTORE_GIVE_UP = timedelta(minutes=30)

_SIDECAR_TAILS = (
    ".nfo",
    ".srt",
    ".ass",
    ".ssa",
    ".sub",
    ".idx",
    "-thumb.jpg",
    "-thumb.png",
    "-fanart.jpg",
    "-poster.jpg",
)


class AlignError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class _View:
    jellyfin_id: str
    media_type: str
    name: str | None
    year: int | None
    tmdb: int | None
    imdb: str | None
    tvdb: int | None
    saved_at: datetime | None = None


@dataclass(frozen=True)
class _LinkPlan:
    item_id: int | None
    season: int | None
    episode: int | None
    jellyfin_item_id: str
    downloaded: bool
    watched: bool | None
    last_played_at: datetime | None
    file_path: str | None
    title: str | None


def open_client(db: Session, settings: Settings) -> tuple[JellyfinClient, str, str] | None:
    url, api_key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    if not url or not api_key:
        return None
    return JellyfinClient(url, api_key, user_id or None), prefix, path_map


def preview(db: Session, settings: Settings, client: JellyfinClient | None) -> dict:
    """Dry-run. A running scan returns status only, with empty changes."""
    if client is None:
        return _shell(db, scanning=False, progress=None, message="Jellyfin n'est pas configuré.", changes=False)
    status = read_scan_status(client)
    if status.scanning:
        return _shell(db, scanning=True, progress=status.progress, message=status.message, changes=False)
    report = build_report(db, settings)
    report["scanning"] = False
    report["progress"] = None
    report["message"] = status.message
    return report


def refresh_cache(
    db: Session,
    settings: Settings,
    client: JellyfinClient,
    *,
    full: bool = False,
    now: datetime | None = None,
    tmdb=None,
) -> dict:
    """Upsert the catalog. Refuses to move the watermark while Jellyfin is scanning."""
    status = read_scan_status(client)
    if status.scanning:
        report = _shell(db, scanning=True, progress=status.progress, message=status.message, changes=False)
        report["refreshed"] = 0
        return report
    moment = now or datetime.now(timezone.utc)
    _url, _key, user_id, prefix, path_map = jellyfin_settings(db, settings)
    resolved = resolve_user_id(client, user_id)
    since = None if full else _text(db, LIBRARY_SYNCED) or None
    raw = client.catalog_items(resolved, since=since)
    parsed = [item for item in (parse_catalog_item(row) for row in raw) if item is not None]
    parsed = [_fill_tmdb(item, tmdb) for item in parsed]
    if full:
        db.execute(delete(JellyfinLibraryItem))
    mappings = parse_path_map(path_map)
    skip = library_skip(storage_layout(db, settings))
    stored = 0
    for item in parsed:
        local = _local_path(item.path, mappings, prefix)
        if _reserved_library_path(local or item.path, skip):
            row = db.get(JellyfinLibraryItem, item.jellyfin_id)
            if row is not None:
                db.delete(row)
            continue
        _upsert_item(db, item, local, moment)
        stored += 1
    saved = [item.date_last_saved for item in parsed if item.date_last_saved is not None]
    watermark = max(saved) if saved else moment
    _put(db, LIBRARY_SYNCED, watermark.isoformat(), moment)
    db.flush()
    report = build_report(db, settings)
    report["scanning"] = False
    report["progress"] = None
    note = status.message
    if not full and since:
        note = f"{note} Lecture incrémentale. Les retraits Jellyfin apparaissent après un rafraîchissement complet."
    report["message"] = note
    report["refreshed"] = stored
    return report


def apply_alignment(
    db: Session,
    settings: Settings,
    client: JellyfinClient | None,
    *,
    validated: bool,
    now: datetime | None = None,
) -> dict:
    """Write fiche identity. Files stay where they are."""
    current = preview(db, settings, client)
    if not validated:
        current["applied"] = False
        return current
    if client is None:
        raise AlignError("NOT_CONFIGURED", "Jellyfin n'est pas configuré.")
    if current["scanning"]:
        raise AlignError("SCANNING", current["message"])
    moment = now or datetime.now(timezone.utc)
    plan = build_report(db, settings)
    for merge in plan["merges"]:
        _apply_merge(db, merge, moment)
    for change in plan["reidentifies"]:
        _apply_identity(db, change, moment, reidentify=True)
    for change in plan["attaches"]:
        _apply_identity(db, change, moment, reidentify=False)
    from mediatracker.library.jellyfin_push import enqueue_identify

    for row in plan["holds"]:
        media = db.get(Media, row["media_id"])
        if media is not None and media.tmdb_id is not None:
            enqueue_identify(db, media, moment, previous_tmdb=row.get("jellyfin_tmdb"))
    db.flush()
    fresh = build_report(db, settings)
    fresh["scanning"] = False
    fresh["progress"] = None
    fresh["message"] = "Identification alignée. Aucun fichier n'a été déplacé."
    fresh["applied"] = True
    return fresh


def apply_renames(
    db: Session,
    settings: Settings,
    client: JellyfinClient | None,
    *,
    validated: bool,
    automatic: bool = False,
    media_ids: list[int] | None = None,
    now: datetime | None = None,
) -> dict:
    """Move files into ``Title (Year) [tmdbid-N]/Season NN``. Then ask Jellyfin to rescan those paths."""
    if client is not None and read_scan_status(client).scanning:
        raise AlignError("SCANNING", read_scan_status(client).message)
    if (validated or automatic) and client is None:
        raise AlignError("NOT_CONFIGURED", "Jellyfin n'est pas configuré.")
    if not validated and not (automatic and auto_rename_enabled(db)):
        report = build_report(db, settings)
        report["applied"] = False
        report["moved"] = 0
        return report
    moment = now or datetime.now(timezone.utc)
    wanted = set(media_ids) if media_ids is not None else None
    moves = [
        row
        for row in build_report(db, settings)["renames"]
        if not row["blocked"] and (wanted is None or row["media_id"] in wanted)
    ]
    moved = commit_renames(db, settings, client, moves, moment)
    fresh = build_report(db, settings)
    fresh["applied"] = True
    fresh["moved"] = moved["count"]
    fresh["refresh"] = moved["refresh"]
    fresh["message"] = (
        "Rangement appliqué. Jellyfin est rafraîchi sur les nouveaux chemins."
        if moved["count"]
        else "Rien à renommer."
    )
    return fresh


def commit_renames(
    db: Session,
    settings: Settings,
    client: JellyfinClient | None,
    moves: list[dict],
    now: datetime,
) -> dict:
    """Snapshot watched state, rename, then a targeted refresh. The worker waits for the scan."""
    moves = [move for move in moves if different_tmdb_ids(move["src"], move["dest"]) is None]
    if not moves:
        return {"count": 0, "refresh": None}
    _snapshot_moves(db, moves, now)
    applied: list[dict[str, str]] = []
    journal: list[dict[str, str]] = []
    try:
        for move in moves:
            _move_one(db, move, applied, journal)
    except OSError as exc:
        _rollback_paths(applied)
        raise AlignError("MOVE", "Déplacement impossible.") from exc
    if journal:
        db.add(LibraryRename(created_at=now, moves=journal))
        db.flush()
        from mediatracker.core.cleanup_classify import request_classify

        request_classify(db, settings)
    refresh = "library"
    if client is not None:
        refresh = trigger_refresh(client, db, settings, moves)
        arm_restore(db, now)
        _put(db, ALIGN_PENDING, "1", now)
        _put(db, ALIGN_SAW, "", now)
        _put(db, REFRESH_FIRED, now.isoformat(), now)
    return {"count": len(journal), "refresh": refresh}


def trigger_refresh(client: JellyfinClient, db: Session, settings: Settings, moves: list[dict]) -> str:
    """Item refresh and a path notification. The whole library is the fallback."""
    _url, _key, _user, _prefix, path_map = jellyfin_settings(db, settings)
    mappings = parse_path_map(path_map)
    created: list[str] = []
    removed: list[str] = []
    item_ids: list[str] = []
    for move in moves:
        new_path = untranslate_path(move["dest"], mappings) or move["dest"]
        old_path = untranslate_path(move["src"], mappings) or move["src"]
        created.append(new_path)
        removed.append(old_path)
        if move.get("jellyfin_id"):
            item_ids.append(str(move["jellyfin_id"]))
    targeted = False
    if removed:
        try:
            client.notify_updated(removed, "Deleted")
            targeted = True
        except JellyfinError as exc:
            logger.info("Notification Jellyfin (ancien chemin) ignorée (%s).", exc.code)
    if created:
        try:
            client.notify_updated(created, "Created")
            targeted = True
        except JellyfinError as exc:
            logger.info("Notification Jellyfin (nouveau chemin) ignorée (%s).", exc.code)
    for item_id in dict.fromkeys(item_ids):
        try:
            client.refresh_item(item_id, replace_metadata=False)
            targeted = True
        except JellyfinError as exc:
            logger.info("Rafraîchissement de l'élément %s ignoré (%s).", item_id, exc.code)
    if targeted:
        return "item"
    client.refresh_library()
    return "library"


def arm_restore(db: Session, now: datetime) -> None:
    _put(db, RESTORE_PENDING, "1", now)
    _put(db, RESTORE_SAW, "", now)
    _put(db, RESTORE_AT, now.isoformat(), now)


def _align_work_due(db: Session, now: datetime) -> bool:
    """True when align may need Jellyfin HTTP (same idea as push's due job, sync's poll)."""
    from mediatracker.library.identity_sync import IDENTITY_DUE

    if _flag(db, RESTORE_PENDING) or _flag(db, ALIGN_PENDING):
        return True
    if _flag(db, IDENTITY_DUE):
        return True
    return _cache_due(db, now)


def maybe_align_jellyfin(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    client: JellyfinClient | None = None,
    tmdb=None,
) -> None:
    """Refresh the cache once a scan is idle, adopt a newer Jellyfin id, and rename when the setting is on.

    Merges stay on the Bibliothèque screen. A newer Jellyfin TMDB id is adopted here.
    A newer MediaTracker edit stays, and its push job is left to the push worker.
    """
    moment = now or datetime.now(timezone.utc)
    if not _align_work_due(db, moment):
        db.commit()
        return
    opened = open_client(db, settings)
    if opened is None and client is None:
        return
    owns = client is None
    prefix = ""
    path_map = ""
    if client is None and opened is not None:
        client, prefix, path_map = opened
    elif opened is not None:
        _client, prefix, path_map = opened
    assert client is not None
    try:
        status = read_scan_status(client)
        from mediatracker.metro.instrument import instrument_jellyfin_wait_scan
        from mediatracker.metro.record import safe_instrument

        if not status.scanning:
            safe_instrument(instrument_jellyfin_wait_scan, scanning=False)
        if status.scanning:
            if _flag(db, ALIGN_PENDING) or _flag(db, RESTORE_PENDING):
                _put(db, ALIGN_SAW, "1", moment)
                _put(db, RESTORE_SAW, "1", moment)
            safe_instrument(instrument_jellyfin_wait_scan, scanning=True)
            # A return inside try skips the commit below. The worker closes the session
            # afterwards, so the "we saw the scan" flags have to be stored here.
            db.commit()
            return
        if _flag(db, RESTORE_PENDING) and _wait_done(db, RESTORE_AT, RESTORE_SAW, moment):
            try:
                refresh_cache(db, settings, client, full=False, now=moment, tmdb=tmdb)
            except JellyfinError as exc:
                logger.info("Cache Jellyfin non relu avant restauration (%s).", exc.code)
            else:
                _restore_watched(db, client, settings, moment)
        from mediatracker.library.identity_sync import IDENTITY_DUE

        due = _flag(db, IDENTITY_DUE) or _cache_due(db, moment)
        refreshed = False
        if due:
            full = not _text(db, LIBRARY_SYNCED) and not _flag(db, IDENTITY_DUE)
            try:
                refresh_cache(db, settings, client, full=full, now=moment, tmdb=tmdb)
            except JellyfinError as exc:
                logger.info("Lecture Jellyfin reportée (%s).", exc.code)
            else:
                refreshed = True
                _put(db, IDENTITY_DUE, "", moment)
                _link_paths(db, moment)
                if _flag(db, ALIGN_PENDING):
                    _put(db, ALIGN_PENDING, "", moment)
        adopted = _adopt_newer(db, settings, moment)
        if auto_rename_enabled(db) and (refreshed or adopted):
            try:
                apply_renames(db, settings, client, validated=False, automatic=True, now=moment)
            except AlignError as exc:
                logger.info("Renommage automatique reporté (%s).", exc.code)
    finally:
        if owns:
            client.close()
        elif opened is not None and opened[0] is not client:
            opened[0].close()
    db.commit()


def auto_rename_enabled(db: Session) -> bool:
    row = db.get(Setting, AUTO_RENAME)
    return bool(row and row.value == "true")


def cached_for_local_path(db: Session, path: Path) -> JellyfinLibraryItem | None:
    """A catalog row whose path is this file. The folder scan must not invent another fiche."""
    try:
        resolved = str(path.resolve())
    except OSError:
        resolved = str(path)
    wanted = {_norm(resolved), _norm(str(path))}
    rows = list(db.scalars(select(JellyfinLibraryItem)).all())
    for row in rows:
        for candidate in (row.local_path, row.path):
            if candidate and _norm(candidate) in wanted:
                return row
    return None


def media_for_cache(db: Session, cached: JellyfinLibraryItem) -> Media | None:
    series_id = cached.series_id or (cached.jellyfin_id if cached.item_type in {"Series", "Movie"} else None)
    if series_id:
        linked = db.scalar(select(Media).where(Media.jellyfin_series_id == series_id))
        if linked is not None:
            return linked
    identity = cached
    if cached.item_type not in {"Series", "Movie"} and cached.series_id:
        parent = db.get(JellyfinLibraryItem, cached.series_id)
        if parent is not None:
            identity = parent
    if identity.tmdb_id is None:
        return None
    kind = "film" if identity.item_type == "Movie" else "series"
    return db.scalar(select(Media).where(Media.tmdb_id == identity.tmdb_id, Media.type == kind))


def build_report(db: Session, settings: Settings) -> dict:
    movies, series = library_roots(db, settings)
    rows = list(db.scalars(select(JellyfinLibraryItem)).all())
    medias = list(db.scalars(select(Media)).all())
    views = _views(rows)
    by_series: dict[str, list[JellyfinLibraryItem]] = {}
    for row in rows:
        if row.item_type in {"Episode", "Movie"} and row.series_id:
            by_series.setdefault(row.series_id, []).append(row)
    attaches: list[dict] = []
    reidentifies: list[dict] = []
    merges: list[dict] = []
    holds: list[dict] = []
    matched: set[int] = set()
    blocked_merge: set[int] = set()
    blocked_identity: set[int] = set()
    for view in views:
        hits = _match_fiches(medias, view)
        for hit in hits:
            matched.add(hit.id)
        if len(hits) > 1:
            keeper = _keeper(hits)
            drops = [row.id for row in hits if row.id != keeper.id]
            merges.append(
                {
                    "keep_id": keeper.id,
                    "drop_ids": drops,
                    "jellyfin_id": view.jellyfin_id,
                    "title": view.name or keeper.title,
                    "tmdb_id": view.tmdb,
                }
            )
            blocked_merge.update(row.id for row in hits)
            continue
        if len(hits) != 1:
            continue
        media = hits[0]
        links = _links_for(db, media, by_series.get(view.jellyfin_id, []), view)
        decision = _decision(media, view)
        if decision == "push":
            holds.append(
                {
                    "media_id": media.id,
                    "title": media.title,
                    "tmdb_id": media.tmdb_id,
                    "jellyfin_tmdb": view.tmdb,
                    "jellyfin_id": view.jellyfin_id,
                }
            )
            if links:
                attaches.append(_attach_row(media, view, links))
            continue
        if decision == "adopt":
            reidentifies.append(_identity_row(media, view, links))
            blocked_identity.add(media.id)
            continue
        if _attach_needed(media, view, links):
            attaches.append(_attach_row(media, view, links))
    orphans, cleaned_after_watch = _orphans(db, medias, matched, bool(views))
    renames = _rename_plan(db, movies, series, rows, medias, blocked_merge, blocked_identity)
    pushes = _push_rows(db)
    return {
        "scanning": False,
        "progress": None,
        "message": "Jellyfin n'analyse pas la bibliothèque.",
        "auto_rename": auto_rename_enabled(db),
        "synced_at": _text(db, LIBRARY_SYNCED) or None,
        "attaches": attaches,
        "reidentifies": reidentifies,
        "merges": merges,
        "orphans": orphans,
        "cleaned_after_watch": cleaned_after_watch,
        "holds": holds,
        "renames": renames,
        "pushes": pushes,
    }


def _shell(db: Session, *, scanning: bool, progress: int | None, message: str, changes: bool) -> dict:
    del changes
    return {
        "scanning": scanning,
        "progress": progress,
        "message": message,
        "auto_rename": auto_rename_enabled(db),
        "synced_at": _text(db, LIBRARY_SYNCED) or None,
        "attaches": [],
        "reidentifies": [],
        "merges": [],
        "orphans": [],
        "cleaned_after_watch": [],
        "holds": [],
        "renames": [],
        "pushes": _push_rows(db),
    }


def _views(rows: list[JellyfinLibraryItem]) -> list[_View]:
    found: list[_View] = []
    for row in rows:
        if row.item_type not in {"Series", "Movie"}:
            continue
        found.append(
            _View(
                jellyfin_id=row.jellyfin_id,
                media_type="film" if row.item_type == "Movie" else "series",
                name=row.name,
                year=row.year,
                tmdb=row.tmdb_id,
                imdb=row.imdb_id,
                tvdb=row.tvdb_id,
                saved_at=row.date_last_saved,
            )
        )
    return found


def _match_fiches(medias: list[Media], view: _View) -> list[Media]:
    hits: list[Media] = []
    seen: set[int] = set()
    for media in medias:
        if media.type != view.media_type or media.id in seen:
            continue
        if (media.jellyfin_series_id or "") == view.jellyfin_id:
            hits.append(media)
            seen.add(media.id)
            continue
        if view.tmdb is not None and media.tmdb_id == view.tmdb:
            hits.append(media)
            seen.add(media.id)
            continue
        if view.imdb and media.imdb_id and media.imdb_id.casefold() == view.imdb.casefold():
            hits.append(media)
            seen.add(media.id)
            continue
        if view.tvdb is not None and media.tvdb_id == view.tvdb:
            hits.append(media)
            seen.add(media.id)
    return hits


def _decision(media: Media, view: _View) -> str:
    """Last write wins only when both sides already have a TMDB id."""
    if view.tmdb is None or media.tmdb_id == view.tmdb:
        return "agree"
    if media.tmdb_id is None and media.identity_edited_at is None:
        return "agree"
    from mediatracker.library.identity_sync import classify

    return classify(
        media_tmdb=media.tmdb_id,
        edited_at=media.identity_edited_at,
        echo_tmdb=media.identity_echo_tmdb,
        echo_previous=media.identity_echo_previous,
        echo_at=media.identity_echo_at,
        jellyfin_tmdb=view.tmdb,
        jellyfin_saved=view.saved_at,
    )


def _adopt_newer(db: Session, settings: Settings, now: datetime) -> int:
    """Apply Jellyfin ids that are newer than the fiche. Queue a push when MediaTracker is newer."""
    from mediatracker.library.jellyfin_push import enqueue_identify

    report = build_report(db, settings)
    adopted = 0
    for change in report["reidentifies"]:
        media = db.get(Media, change["media_id"])
        before = media.tmdb_id if media is not None else None
        _apply_identity(db, change, now, reidentify=True)
        if media is not None and media.tmdb_id != before:
            adopted += 1
    for hold in report["holds"]:
        media = db.get(Media, hold["media_id"])
        if media is None or media.tmdb_id is None:
            continue
        enqueue_identify(db, media, now, previous_tmdb=hold.get("jellyfin_tmdb"), retry_failed=False)
    return adopted


def _aware(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _keeper(hits: list[Media]) -> Media:
    edited = [row for row in hits if row.identity_edited_at is not None]
    if edited:
        return max(edited, key=lambda row: (_aware(row.identity_edited_at), -row.id))
    linked = [row for row in hits if (row.jellyfin_series_id or "").strip()]
    if linked:
        return min(linked, key=lambda row: row.id)
    return min(hits, key=lambda row: row.id)


def _links_for(db: Session, media: Media, episodes: list[JellyfinLibraryItem], view: _View) -> list[_LinkPlan]:
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    plans: list[_LinkPlan] = []
    for episode in episodes:
        if view.media_type == "film" and episode.item_type != "Movie":
            continue
        if view.media_type == "series" and episode.item_type != "Episode":
            continue
        item = _slot(items, episode)
        watched = _watched_plan(item, episode.played, episode.last_played_at)
        path = episode.local_path or None
        plans.append(
            _LinkPlan(
                item_id=item.id if item is not None else None,
                season=episode.season if view.media_type == "series" else None,
                episode=episode.episode if view.media_type == "series" else None,
                jellyfin_item_id=episode.jellyfin_id,
                downloaded=True,
                watched=watched,
                last_played_at=episode.last_played_at,
                file_path=path if item is None or not item.file_path else None,
                title=episode.name,
            )
        )
    return [plan for plan in plans if _link_changes(items, plan)]


def _slot(items: list[MediaItem], episode: JellyfinLibraryItem) -> MediaItem | None:
    for item in items:
        if item.jellyfin_item_id and item.jellyfin_item_id == episode.jellyfin_id:
            return item
    local = _norm(episode.local_path or "")
    remote = _norm(episode.path or "")
    for item in items:
        current = _norm(item.file_path or "")
        if current and current in {local, remote}:
            return item
    if episode.item_type == "Movie":
        films = [item for item in items if item.season is None and item.episode is None]
        return films[0] if len(films) == 1 else None
    if episode.season is None or episode.episode is None:
        return None
    slotted = [item for item in items if item.season == episode.season and item.episode == episode.episode]
    return slotted[0] if slotted else None


def _watched_plan(item: MediaItem | None, played: bool | None, played_at: datetime | None) -> bool | None:
    if played is None:
        return None
    if item is not None and item.watched == played:
        return None
    if item is not None:
        from mediatracker.core.watch_manual_lock import jellyfin_blocks_manual

        if jellyfin_blocks_manual(item, played, played_at):
            return None
    return played


def _link_changes(items: list[MediaItem], plan: _LinkPlan) -> bool:
    if plan.item_id is None:
        return True
    item = next((row for row in items if row.id == plan.item_id), None)
    if item is None:
        return True
    if item.jellyfin_item_id != plan.jellyfin_item_id:
        return True
    if plan.downloaded and not item.downloaded:
        return True
    if plan.watched is not None and item.watched != plan.watched:
        return True
    return False


def _attach_needed(media: Media, view: _View, links: list[_LinkPlan]) -> bool:
    if (media.jellyfin_series_id or "") != view.jellyfin_id:
        return True
    if view.tmdb is not None and media.tmdb_id is None:
        return True
    return bool(links)


def _attach_row(media: Media, view: _View, links: list[_LinkPlan]) -> dict:
    return _identity_row(media, view, links)


def _identity_row(media: Media, view: _View, links: list[_LinkPlan]) -> dict:
    return {
        "media_id": media.id,
        "title": media.title,
        "jellyfin_id": view.jellyfin_id,
        "from_tmdb": media.tmdb_id,
        "to_tmdb": view.tmdb,
        "name": view.name,
        "year": view.year,
        "imdb_id": view.imdb,
        "tvdb_id": view.tvdb,
        "items": [
            {
                "item_id": link.item_id,
                "season": link.season,
                "episode": link.episode,
                "jellyfin_item_id": link.jellyfin_item_id,
                "downloaded": link.downloaded,
                "watched": link.watched,
                "file_path": link.file_path,
                "title": link.title,
            }
            for link in links
        ],
        "saved_at": view.saved_at,
    }


def _orphans(
    db: Session,
    medias: list[Media],
    matched: set[int],
    have_views: bool,
) -> tuple[list[dict], list[dict]]:
    if not have_views:
        return [], []
    from mediatracker.core.file_status import FileStatusIndex

    orphans: list[dict] = []
    cleaned: list[dict] = []

    def _title_key(row: dict) -> str:
        return row["title"].casefold()

    for media in medias:
        if media.id in matched:
            continue
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        if not any(item.downloaded or item.file_path for item in items):
            continue
        index = FileStatusIndex.load(db, items)
        tracked = [item for item in items if item.downloaded or item.file_path]
        if tracked and all(index.for_item(item) in {"trash", "absent"} for item in tracked):
            if all(item.watched for item in tracked):
                cleaned.append({"media_id": media.id, "title": media.title})
                continue
        orphans.append({"media_id": media.id, "title": media.title})
    return sorted(orphans, key=_title_key), sorted(cleaned, key=_title_key)


def _rename_plan(
    db: Session,
    movies: Path,
    series: Path,
    rows: list[JellyfinLibraryItem],
    medias: list[Media],
    blocked_merge: set[int],
    blocked_identity: set[int],
) -> list[dict]:
    by_id = {row.jellyfin_id: row for row in rows}
    planned: list[dict] = []
    for media in medias:
        view = _view_for_media(media, rows)
        if view is None and media.tmdb_id is None:
            continue
        title, year, tmdb = _rename_identity(media, view)
        if not title:
            continue
        items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        for item in items:
            src = _existing_file(item, by_id)
            if src is None:
                continue
            dest = library_destination(
                movies_dir=movies,
                series_dir=series,
                media_type=media.type,
                title=title,
                year=year,
                season=item.season,
                episode=item.episode,
                ext=src.suffix or ".mkv",
                tmdb_id=tmdb,
            )
            if _norm(str(src)) == _norm(str(dest)):
                continue
            blocked = different_tmdb_ids(src, dest)
            if blocked is None and media.id in blocked_merge:
                blocked = "Fusionnez d'abord les fiches."
            elif blocked is None and media.id in blocked_identity:
                blocked = "Alignez d'abord l'identification."
            elif blocked is None and dest.exists() and dest.resolve() != src.resolve():
                blocked = "Un fichier porte déjà ce nom."
            elif blocked is None and not src.is_file():
                blocked = "Fichier introuvable."
            planned.append(
                {
                    "media_id": media.id,
                    "title": title,
                    "src": str(src),
                    "dest": str(dest),
                    "blocked": blocked,
                    "jellyfin_id": (media.jellyfin_series_id or (view.jellyfin_id if view else "")) or "",
                }
            )
    return planned


def _view_for_media(media: Media, rows: list[JellyfinLibraryItem]) -> _View | None:
    for view in _views(rows):
        if media.type != view.media_type:
            continue
        if (media.jellyfin_series_id or "") == view.jellyfin_id:
            return view
        if view.tmdb is not None and media.tmdb_id == view.tmdb:
            return view
    return None


def _rename_identity(media: Media, view: _View | None) -> tuple[str, int | None, int | None]:
    """The folder uses the identity that last-write-wins would keep."""
    if view is None or _decision(media, view) != "adopt":
        return media.title, media.year, media.tmdb_id
    return view.name or media.title, view.year if view.year is not None else media.year, view.tmdb or media.tmdb_id


def _existing_file(item: MediaItem, by_id: dict[str, JellyfinLibraryItem]) -> Path | None:
    if item.file_path:
        path = Path(item.file_path)
        if path.is_file():
            return path
    if item.jellyfin_item_id and item.jellyfin_item_id in by_id:
        local = by_id[item.jellyfin_item_id].local_path
        if local and Path(local).is_file():
            return Path(local)
    return None


def _push_rows(db: Session) -> list[dict]:
    from mediatracker.db.models import JellyfinPushJob
    from mediatracker.library.jellyfin_push import MAX_ATTEMPTS

    rows = list(db.scalars(select(JellyfinPushJob).order_by(JellyfinPushJob.id.desc()).limit(20)).all())
    return [
        {
            "media_id": row.media_id,
            "tmdb_id": row.tmdb_id,
            "state": row.state,
            "attempts": row.attempts,
            "last_error": row.last_error,
            "max_attempts": MAX_ATTEMPTS,
        }
        for row in rows
        if row.state != "done"
    ]


def _apply_merge(db: Session, merge: dict, now: datetime) -> None:
    keeper = db.get(Media, merge["keep_id"])
    if keeper is None:
        return
    for drop_id in merge["drop_ids"]:
        loser = db.get(Media, drop_id)
        if loser is None or loser.id == keeper.id:
            continue
        _fold_media(db, keeper, loser)
    if merge.get("jellyfin_id"):
        keeper.jellyfin_series_id = merge["jellyfin_id"]
    db.flush()
    catalog = list(db.scalars(select(JellyfinLibraryItem)).all())
    view = next((item for item in _views(catalog) if item.jellyfin_id == merge.get("jellyfin_id")), None)
    if (
        view is not None
        and merge.get("tmdb_id") is not None
        and keeper.tmdb_id != merge["tmdb_id"]
        and _decision(keeper, view) == "adopt"
    ):
        from mediatracker.library.identity_sync import adopt

        adopt(
            db,
            keeper,
            tmdb_id=merge["tmdb_id"],
            title=view.name,
            year=view.year,
            when=view.saved_at or now,
        )
    if view is not None:
        episodes = list(
            db.scalars(select(JellyfinLibraryItem).where(JellyfinLibraryItem.series_id == view.jellyfin_id)).all()
        )
        for link in _links_for(db, keeper, episodes, view):
            _apply_link(
                db,
                keeper,
                {
                    "item_id": link.item_id,
                    "season": link.season,
                    "episode": link.episode,
                    "jellyfin_item_id": link.jellyfin_item_id,
                    "downloaded": link.downloaded,
                    "watched": link.watched,
                    "last_played_at": link.last_played_at,
                    "file_path": link.file_path,
                    "title": link.title,
                },
                now,
            )
    keeper.tmdb_refreshed_at = now
    db.flush()


def _fold_media(db: Session, keeper: Media, loser: Media) -> None:
    slots = {(item.season, item.episode): item for item in list(keeper.items)}
    for item in list(loser.items):
        slot = (item.season, item.episode)
        current = slots.get(slot)
        if current is None:
            item.media_id = keeper.id
            slots[slot] = item
            continue
        _fold_item(current, item)
        _reassign_item(db, item, current)
        db.flush()
        db.delete(item)
    numbers = {season.number for season in keeper.seasons}
    for season in list(loser.seasons):
        if season.number in numbers:
            db.delete(season)
        else:
            season.media_id = keeper.id
            numbers.add(season.number)
    for binding in list(db.scalars(select(SourceBinding).where(SourceBinding.media_id == loser.id)).all()):
        binding.media_id = keeper.id
    for event in list(db.scalars(select(Event).where(Event.media_id == loser.id)).all()):
        event.media_id = keeper.id
    for library in list(db.scalars(select(LibraryFile).where(LibraryFile.media_id == loser.id)).all()):
        library.media_id = keeper.id
    db.flush()
    db.expire(loser, ["items", "seasons", "bindings"])
    db.delete(loser)


def _fold_item(keeper: MediaItem, incoming: MediaItem) -> None:
    if not keeper.jellyfin_item_id and incoming.jellyfin_item_id:
        keeper.jellyfin_item_id = incoming.jellyfin_item_id
    if not keeper.file_path and incoming.file_path:
        keeper.file_path = incoming.file_path
    if incoming.downloaded and not keeper.downloaded:
        keeper.downloaded = True
        keeper.downloaded_at = keeper.downloaded_at or incoming.downloaded_at
        if keeper.state == "missing":
            keeper.state = "downloaded"
    if incoming.watched and not keeper.watched:
        keeper.watched = True
        keeper.watched_at = keeper.watched_at or incoming.watched_at
        keeper.state = "watched"


def _reassign_item(db: Session, incoming: MediaItem, keeper: MediaItem) -> None:
    for link in list(incoming.links):
        clash = db.scalar(
            select(Link).where(
                Link.provider_id == link.provider_id,
                Link.external_id == link.external_id,
                Link.id != link.id,
            )
        )
        if clash is not None:
            db.delete(link)
        else:
            link.media_item_id = keeper.id
    for download in list(db.scalars(select(Download).where(Download.media_item_id == incoming.id)).all()):
        download.media_item_id = keeper.id
    for library in list(db.scalars(select(LibraryFile).where(LibraryFile.media_item_id == incoming.id)).all()):
        library.media_item_id = keeper.id
        library.media_id = keeper.media_id


def _apply_identity(db: Session, change: dict, now: datetime, *, reidentify: bool) -> None:
    media = db.get(Media, change["media_id"])
    if media is None:
        return
    if change.get("jellyfin_id"):
        media.jellyfin_series_id = change["jellyfin_id"]
    if reidentify and change.get("to_tmdb") is not None and media.tmdb_id != change["to_tmdb"]:
        from mediatracker.library.identity_sync import adopt

        saved = change.get("saved_at") if isinstance(change.get("saved_at"), datetime) else now
        adopt(
            db,
            media,
            tmdb_id=change["to_tmdb"],
            title=change.get("name"),
            year=change.get("year"),
            when=saved,
        )
    elif media.tmdb_id is None and change.get("to_tmdb") is not None and media.identity_edited_at is None:
        media.tmdb_id = change["to_tmdb"]
    if change.get("imdb_id") and not media.imdb_id:
        media.imdb_id = change["imdb_id"]
    if change.get("tvdb_id") is not None and media.tvdb_id is None:
        media.tvdb_id = change["tvdb_id"]
    for raw in change.get("items") or []:
        _apply_link(db, media, raw, now)
    db.flush()


def _apply_link(db: Session, media: Media, raw: dict, now: datetime) -> None:
    item = db.get(MediaItem, raw["item_id"]) if raw.get("item_id") else None
    if item is None:
        item = MediaItem(
            media_id=media.id,
            season=raw.get("season"),
            episode=raw.get("episode"),
            title=raw.get("title"),
            state="downloaded" if raw.get("downloaded") else "missing",
            downloaded=bool(raw.get("downloaded")),
            downloaded_at=now if raw.get("downloaded") else None,
        )
        db.add(item)
        db.flush()
    if raw.get("jellyfin_item_id"):
        item.jellyfin_item_id = raw["jellyfin_item_id"]
    if raw.get("downloaded") and not item.downloaded:
        item.downloaded = True
        item.downloaded_at = item.downloaded_at or now
        if item.state == "missing":
            item.state = "downloaded"
    if raw.get("file_path") and not item.file_path:
        item.file_path = raw["file_path"]
    watched = raw.get("watched")
    if watched is None or item.watched == watched:
        return
    from mediatracker.core.watch_manual_lock import clear_manual_lock, jellyfin_blocks_manual

    played_at = raw.get("last_played_at")
    if jellyfin_blocks_manual(item, bool(watched), played_at):
        return
    if item.watch_source == "manual":
        clear_manual_lock(item)
    item.watched = bool(watched)
    if watched:
        item.watched_at = item.watched_at or now
        item.state = "watched"
    else:
        item.watched_at = None
        if item.state == "watched":
            item.state = "downloaded" if item.downloaded else "missing"


def _link_paths(db: Session, now: datetime) -> int:
    """Point an existing file at the Jellyfin item. Does not change a TMDB id or merge fiches."""
    changed = 0
    rows = list(
        db.scalars(
            select(JellyfinLibraryItem).where(JellyfinLibraryItem.item_type.in_(("Episode", "Movie")))
        ).all()
    )
    items = list(db.scalars(select(MediaItem)).all())
    medias = {row.id: row for row in db.scalars(select(Media)).all()}
    for cached in rows:
        item = _slot(items, cached)
        if item is None:
            continue
        media = medias.get(item.media_id)
        if cached.jellyfin_id and item.jellyfin_item_id != cached.jellyfin_id:
            item.jellyfin_item_id = cached.jellyfin_id
            changed += 1
        if not item.downloaded:
            item.downloaded = True
            item.downloaded_at = item.downloaded_at or now
            if item.state == "missing":
                item.state = "downloaded"
            changed += 1
        # Jellyfin « vu » is applied by jellyfin_sync (poll/webhook). The catalog cache can lag
        # behind UserData after a watch; do not clear local watched state from stale cache rows.
        if cached.played is True:
            watched = _watched_plan(item, True, cached.last_played_at)
            if watched is not None:
                from mediatracker.core.watch_manual_lock import clear_manual_lock

                if item.watch_source == "manual":
                    clear_manual_lock(item)
                item.watched = watched
                item.watched_at = item.watched_at or now
                item.state = "watched"
                changed += 1
        if media is None or not cached.series_id:
            continue
        parent = db.get(JellyfinLibraryItem, cached.series_id)
        series_tmdb = parent.tmdb_id if parent is not None else None
        if media.jellyfin_series_id:
            continue
        if series_tmdb is not None and media.tmdb_id not in {None, series_tmdb}:
            continue
        media.jellyfin_series_id = cached.series_id
        changed += 1
    return changed


def _snapshot_moves(db: Session, moves: list[dict], now: datetime) -> None:
    sources = {row["src"] for row in moves}
    dest_of = {row["src"]: row["dest"] for row in moves}
    items = list(db.scalars(select(MediaItem).where(MediaItem.file_path.in_(sources))).all())
    cache = {row.jellyfin_id: row for row in db.scalars(select(JellyfinLibraryItem)).all()}
    for item in items:
        cached = cache.get(item.jellyfin_item_id or "")
        played = bool(item.watched) or bool(cached and cached.played)
        db.add(
            JellyfinWatchSnapshot(
                media_item_id=item.id,
                jellyfin_item_id=item.jellyfin_item_id or "",
                played=played,
                path=dest_of.get(item.file_path or "", item.file_path),
                created_at=now,
            )
        )
    db.flush()


def _restore_watched(db: Session, client: JellyfinClient, settings: Settings, now: datetime) -> int:
    snaps = list(
        db.scalars(select(JellyfinWatchSnapshot).where(JellyfinWatchSnapshot.restored_at.is_(None))).all()
    )
    if not snaps:
        _clear_restore(db, now)
        return 0
    _url, _key, user_id, _prefix, path_map = jellyfin_settings(db, settings)
    try:
        resolved = resolve_user_id(client, user_id)
        raw = client.catalog_items(resolved)
    except JellyfinError as exc:
        logger.info("Restauration des vus reportée : %s", exc.message)
        return 0
    parsed = [item for item in (parse_catalog_item(row) for row in raw) if item is not None]
    mappings = parse_path_map(path_map)
    by_id = {item.jellyfin_id: item for item in parsed}
    by_local: dict[str, CatalogItem] = {}
    for item in parsed:
        local = _local_path(item.path, mappings, "")
        if local:
            by_local[_norm(local)] = item
    restored = 0
    pending = 0
    for snap in snaps:
        if not snap.played:
            snap.restored_at = now
            continue
        current = by_id.get(snap.jellyfin_item_id) if snap.jellyfin_item_id else None
        if current is None and snap.path:
            current = by_local.get(_norm(snap.path))
        if current is None:
            created = snap.created_at if snap.created_at.tzinfo else snap.created_at.replace(tzinfo=timezone.utc)
            if now - created >= RESTORE_GIVE_UP:
                snap.restored_at = now
                logger.info("Vu non restauré : l'élément Jellyfin n'est pas revenu.")
            else:
                pending += 1
            continue
        if current.played is True:
            snap.restored_at = now
            continue
        try:
            client.mark_played(resolved, current.jellyfin_id)
        except JellyfinError as exc:
            logger.info("Vu Jellyfin non restauré (%s).", exc.code)
            pending += 1
            continue
        snap.restored_at = now
        restored += 1
        logger.info("Vu restauré sur Jellyfin (%s).", current.jellyfin_id)
    if pending == 0:
        _clear_restore(db, now)
    return restored


def _move_one(db: Session, move: dict, applied: list[dict[str, str]], journal: list[dict[str, str]]) -> None:
    src = Path(move["src"])
    dest = Path(move["dest"])
    if different_tmdb_ids(src, dest):
        return
    if not src.is_file() or src.resolve() == dest.resolve() or dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_move(src, dest)
    retarget_path(db, src, dest)
    recorded = {"src": str(src), "dest": str(dest)}
    applied.append(recorded)
    journal.append(recorded)
    for sidecar in _sidecars(src):
        side_dest = _sidecar_dest(src, dest, sidecar)
        if side_dest.exists() or not sidecar.is_file():
            continue
        side_dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_move(sidecar, side_dest)
        side = {"src": str(sidecar), "dest": str(side_dest)}
        applied.append(side)
        journal.append(side)
    _prune_empty(src.parent, dest.parents)


def _sidecars(video: Path) -> list[Path]:
    folder = video.parent
    if not folder.is_dir():
        return []
    found: list[Path] = []
    for child in folder.iterdir():
        if not child.is_file() or child.resolve() == video.resolve():
            continue
        name = child.name
        tail_match = any(name.startswith(video.stem) and name.endswith(tail) for tail in _SIDECAR_TAILS)
        if name.startswith(video.name) or tail_match:
            found.append(child)
    return found


def _sidecar_dest(video: Path, dest: Path, sidecar: Path) -> Path:
    name = sidecar.name
    if name.startswith(video.name):
        return dest.parent / f"{dest.name}{name[len(video.name):]}"
    if name.startswith(video.stem):
        return dest.parent / f"{dest.stem}{name[len(video.stem):]}"
    return dest.parent / name


def _prune_empty(folder: Path, dest_parents: object) -> None:
    del dest_parents
    current = folder
    for _depth in range(4):
        if not current.is_dir():
            break
        try:
            next(current.iterdir())
        except StopIteration:
            parent = current.parent
            try:
                current.rmdir()
            except OSError:
                break
            current = parent
            continue
        except OSError:
            break
        break


def _cache_due(db: Session, now: datetime) -> bool:
    if _flag(db, ALIGN_PENDING):
        return _wait_done(db, REFRESH_FIRED, ALIGN_SAW, now)
    synced = _parse_time(_text(db, LIBRARY_SYNCED))
    if synced is None:
        return True
    return now - synced >= timedelta(minutes=_poll_minutes(db))


def _wait_done(db: Session, at_key: str, saw_key: str, now: datetime) -> bool:
    if _flag(db, saw_key):
        return True
    started = _parse_time(_text(db, at_key))
    if started is None:
        return at_key != REFRESH_FIRED or not _flag(db, ALIGN_PENDING)
    return now - started >= WAIT_FOR_SCAN


def _fill_tmdb(item: CatalogItem, tmdb) -> CatalogItem:
    if item.tmdb_id is not None or item.item_type not in {"Series", "Movie"} or tmdb is None:
        return item
    finder = getattr(tmdb, "find_external", None)
    if finder is None:
        return item
    wanted = "film" if item.item_type == "Movie" else "series"
    if item.imdb_id:
        found, kind = finder(item.imdb_id, "imdb_id")
        if isinstance(found, int) and kind in {None, wanted}:
            return replace(item, tmdb_id=found)
    if item.tvdb_id is not None:
        found, kind = finder(str(item.tvdb_id), "tvdb_id")
        if isinstance(found, int) and kind in {None, wanted}:
            return replace(item, tmdb_id=found)
    return item


def _upsert_item(db: Session, item: CatalogItem, local: str | None, now: datetime) -> None:
    row = db.get(JellyfinLibraryItem, item.jellyfin_id)
    if row is None:
        row = JellyfinLibraryItem(jellyfin_id=item.jellyfin_id, item_type=item.item_type, synced_at=now)
        db.add(row)
    row.item_type = item.item_type
    row.name = (item.name or "")[:300] or None
    row.year = item.year
    row.series_id = item.series_id
    row.parent_id = item.parent_id
    row.tmdb_id = item.tmdb_id
    row.imdb_id = item.imdb_id
    row.tvdb_id = item.tvdb_id
    row.path = item.path
    row.local_path = local
    row.season = item.season
    row.episode = item.episode
    row.played = item.played
    row.last_played_at = item.last_played_at
    row.date_last_saved = item.date_last_saved
    row.synced_at = now


def _reserved_library_path(path: str | None, skip: tuple[Path, ...]) -> bool:
    """True when the file lives in downloads, incomplete, or the trash."""
    if not path:
        return False
    return is_under(Path(path), skip)


def _local_path(path: str | None, mappings: list[tuple[str, str]], prefix: str) -> str | None:
    del prefix
    if not path:
        return None
    if mappings:
        translated = translate_path(path, mappings)
        if translated:
            return translated
    return path


def _clear_restore(db: Session, now: datetime) -> None:
    _put(db, RESTORE_PENDING, "", now)
    _put(db, RESTORE_SAW, "", now)


def _flag(db: Session, key: str) -> bool:
    return _text(db, key) == "1"


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _put(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
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


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFC", value.replace("\\", "/")).rstrip("/")
    while "//" in text:
        text = text.replace("//", "/")
    return text.casefold()

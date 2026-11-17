"""Local library scanner.

Same role as a source module, but the files are already on disk. A scan is
idempotent: new files are matched, known files are refreshed, missing files
are marked and no longer count as downloaded. It never creates or renames
library folders; a TMDB tag on a parent folder is enough to attach the file.
"""

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.library_guard import reconcile_grouped_absence, reconcile_scoped_absence
from mediatracker.core.metrics.worker_counters import TASK_LIBRARY_SCAN, track_task
from mediatracker.core.storage import is_under, library_skip, storage_layout
from mediatracker.core.tmdb import (
    exact_hit,
    fetch_title,
    hit_years_differ,
    identify,
    open_client,
)
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem, Setting
from mediatracker.db.session import release_sqlite_before_network
from mediatracker.events import record_event
from mediatracker.integrations.tmdb import TmdbError, TmdbTitle
from mediatracker.library.parse import ParsedName, is_video, normalize_title, parse_path, show_key

LAST_SCAN_KEY = "library.last_scan_at"
_ACTIVE_DOWNLOAD = ("pending", "resolving", "queued")


@dataclass(frozen=True)
class ScanReport:
    seen: int
    matched: int
    review: int
    unmatched: int
    missing: int
    followed: int


@dataclass(frozen=True)
class TargetedScanReport:
    found: int
    new: int
    absent: int


class LibraryScanner:
    """Filesystem source. ``scan`` is the only capability; nothing is fetched from the web here."""

    kind = "source"
    type = "library"
    capabilities = frozenset({"local"})

    def __init__(self, movies: Path, series: Path, *, tmdb=None) -> None:
        self.movies = movies
        self.series = series
        self.tmdb = tmdb

    def scan(self, db: Session, *, now: datetime | None = None) -> ScanReport:
        return scan_library(db, movies=self.movies, series=self.series, now=now, tmdb=self.tmdb)


def scan_library(
    db: Session,
    *,
    movies: Path,
    series: Path,
    now: datetime | None = None,
    tmdb=None,
    skip: tuple[Path, ...] = (),
    settings: Settings | None = None,
) -> ScanReport:
    from mediatracker.core.background_priority import apply_background_priority

    apply_background_priority(process_wide=False)
    moment = now or datetime.now(timezone.utc)
    seen: set[str] = set()
    matched = review = unmatched = followed = 0
    for root, kind in ((movies, "film"), (series, "series")):
        if not root.is_dir():
            continue
        for path in _iter_videos(root, skip):
            key = str(path.resolve())
            seen.add(key)
            parsed = parse_path(path, root=root, root_kind=kind)
            outcome, created = _ingest(db, path, parsed, moment, tmdb)
            followed += created
            if outcome == "matched":
                matched += 1
            elif outcome == "review":
                review += 1
            else:
                unmatched += 1
    missing = reconcile_grouped_absence(
        db,
        seen=seen,
        roots=(movies, series),
        now=moment,
        mark_absent=_mark_absent,
    )
    record_event(
        db,
        "library.scanned",
        payload={
            "seen": len(seen),
            "matched": matched,
            "review": review,
            "unmatched": unmatched,
            "missing": missing,
            "followed": followed,
        },
    )
    if settings is not None:
        from mediatracker.core.library_claims import reconcile_library_claims_after_scan

        reconcile_library_claims_after_scan(db, settings, moment)
    return ScanReport(
        seen=len(seen),
        matched=matched,
        review=review,
        unmatched=unmatched,
        missing=missing,
        followed=followed,
    )


def scan_folders(
    db: Session,
    *,
    folders: tuple[Path, ...],
    movies: Path,
    series: Path,
    now: datetime | None = None,
    tmdb=None,
    skip: tuple[Path, ...] = (),
    settings: Settings | None = None,
) -> TargetedScanReport:
    """Scan only the given Jellyfin folders. Does not rename or move anything."""
    moment = now or datetime.now(timezone.utc)
    known_paths = {
        row.path
        for row in db.scalars(select(LibraryFile).where(LibraryFile.status.not_in(("missing", "trashed")))).all()
    }
    seen: set[str] = set()
    new = 0
    for folder in folders:
        if not folder.is_dir():
            continue
        if is_under(folder, (movies,)):
            root = movies
            kind = "film"
        else:
            root = series
            kind = "series"
        for path in _iter_videos(folder, skip):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            if key not in known_paths:
                new += 1
            parsed = parse_path(path, root=root, root_kind=kind)
            _ingest(db, path, parsed, moment, tmdb)
    missing = reconcile_scoped_absence(
        db,
        seen=seen,
        folders=folders,
        now=moment,
        mark_absent=_mark_absent_scoped,
    )
    if settings is not None:
        from mediatracker.core.library_claims import reconcile_library_claims_after_scan

        reconcile_library_claims_after_scan(db, settings, moment)
    return TargetedScanReport(found=len(seen), new=new, absent=missing)


def remember_filed(db: Session, path: Path, media: Media, item: MediaItem, now: datetime) -> LibraryFile:
    """Record a file the pipeline just moved into the library. A later scan stays idempotent."""
    resolved = path.resolve()
    parsed = parse_path(resolved, root=resolved.parent, root_kind=media.type)
    row = _upsert_row(db, resolved, parsed, now)
    row.status = "matched"
    row.confidence = "high"
    row.media_id = media.id
    row.media_item_id = item.id
    row.tmdb_id = media.tmdb_id
    row.missing_at = None
    row.note = None
    row.integrity_suspect = False
    return row


def maybe_scan_library(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    tmdb=None,
) -> ScanReport | None:
    moment = now or datetime.now(timezone.utc)
    if not _setting_bool(db, "library_scan_enabled", True):
        return None
    hours = _setting_int(db, "library_scan_interval_hours", settings.library_scan_interval_hours)
    last = _last_scan(db)
    full_due = last is None or moment - last >= timedelta(hours=hours)
    movies, series = library_roots(db, settings)
    skip = library_skip(storage_layout(db, settings))
    if not full_due:
        from mediatracker.library.incremental import collect_changed_folders, scan_library_incremental

        changed = collect_changed_folders(db, movies=movies, series=series, skip=skip)
        if not changed:
            return None
        client = tmdb
        owns_client = False
        if client is None:
            client, owns_client = open_client(db, settings)
        try:
            with track_task(TASK_LIBRARY_SCAN):
                report = scan_library_incremental(
                    db,
                    movies=movies,
                    series=series,
                    folders=changed,
                    now=moment,
                    tmdb=client,
                    skip=skip,
                    settings=settings,
                )
        finally:
            if owns_client and client is not None:
                client.close()
        db.commit()
        return report
    client = tmdb
    owns_client = False
    if client is None:
        client, owns_client = open_client(db, settings)
    try:
        with track_task(TASK_LIBRARY_SCAN):
            report = scan_library(
                db,
                movies=movies,
                series=series,
                now=moment,
                tmdb=client,
                skip=skip,
                settings=settings,
            )
    finally:
        if owns_client and client is not None:
            client.close()
    from mediatracker.library.incremental import refresh_all_folder_mtimes

    refresh_all_folder_mtimes(db, movies=movies, series=series, skip=skip)
    _touch_last_scan(db, moment)
    db.commit()
    return report


def force_scan(db: Session, settings: Settings, *, now: datetime | None = None) -> ScanReport:
    """Scan now, ignoring the interval. Used at the end of the first-run wizard."""
    moment = now or datetime.now(timezone.utc)
    movies, series = library_roots(db, settings)
    movies.mkdir(parents=True, exist_ok=True)
    series.mkdir(parents=True, exist_ok=True)
    client, owns_client = open_client(db, settings)
    try:
        report = scan_library(
            db,
            movies=movies,
            series=series,
            now=moment,
            tmdb=client,
            skip=library_skip(storage_layout(db, settings)),
            settings=settings,
        )
    finally:
        if owns_client and client is not None:
            client.close()
    from mediatracker.library.incremental import refresh_all_folder_mtimes

    refresh_all_folder_mtimes(db, movies=movies, series=series, skip=library_skip(storage_layout(db, settings)))
    _touch_last_scan(db, moment)
    db.commit()
    return report


def library_roots(db: Session, settings: Settings) -> tuple[Path, Path]:
    layout = storage_layout(db, settings)
    return layout.movies_dir, layout.series_dir


def attach_known_files(db: Session, settings: Settings, media: Media) -> int:
    """Link files already in the library. They count as downloaded and are not queued again."""
    from mediatracker.core.library_plan import list_folders

    movies, series = library_roots(db, settings)
    jellyfin_ids = _jellyfin_folder_ids(db, settings, movies, series)
    now = datetime.now(timezone.utc)
    attached = 0
    for folder in list_folders(movies, series, skip=library_skip(storage_layout(db, settings))):
        tagged = jellyfin_ids.get(str(Path(folder.path).resolve()))
        if folder.media_type != media.type:
            continue
        if not _folder_matches(folder, media) and tagged != media.tmdb_id:
            continue
        root = movies if media.type == "film" else series
        attached += _attach_tree(
            db,
            media,
            Path(folder.path),
            root,
            now,
            bound=Path(folder.path).resolve(),
            skip=library_skip(storage_layout(db, settings)),
        )
    return attached


def bind_library_folder(
    db: Session,
    settings: Settings,
    folder: Path,
    *,
    tmdb_id: int,
    media_type: str,
    media: Media | None = None,
) -> tuple[Path, Media, int]:
    """Stamp ``[tmdbid-N]`` on the folder and attach its videos. Nothing is queued."""
    from mediatracker.core.library_plan import LibraryPlanError, _folder_title
    from mediatracker.core.naming import sanitize

    folder = folder.resolve()
    if not folder.is_dir():
        raise LibraryPlanError("Dossier introuvable.")
    movies, series = library_roots(db, settings)
    library_root = (movies if media_type == "film" else series).resolve()
    from mediatracker.library.identity import show_child_count

    if folder == library_root or show_child_count(folder) >= 2:
        raise LibraryPlanError(
            "Ce dossier contient plusieurs séries. Associez le dossier de la fiche, pas la racine ni un dossier parent."
        )
    title, year = _folder_title(folder.name)
    fetched = fetch_title(db, settings, tmdb_id, media_type)
    if fetched is not None:
        title = fetched.title or title
        year = fetched.year or year
    if media is None:
        media = _media_by_tmdb(db, tmdb_id, media_type)
    if media is None:
        now = datetime.now(timezone.utc)
        from mediatracker.core.tmdb.create import create_from_tmdb

        media, _episodes = create_from_tmdb(
            db,
            media_type=media_type,
            tmdb_id=tmdb_id,
            fetched=fetched,
            now=now,
            title=title or "Inconnu",
            year=year,
            followed=_library_auto_follow(db),
            follow_origin="identification",
            series_status=fetched.series_status if fetched is not None else None,
        )
    elif media.tmdb_id is None:
        media.tmdb_id = tmdb_id
    renamed = _retag_folder(folder, tmdb_id, title or folder.name, year, sanitize)
    root = movies if media_type == "film" else series
    attached = _attach_tree(
        db,
        media,
        renamed,
        root.resolve(),
        datetime.now(timezone.utc),
        bound=renamed.resolve(),
        skip=library_skip(storage_layout(db, settings)),
    )
    return renamed, media, attached


def _attach_tree(
    db: Session,
    media: Media,
    folder: Path,
    root: Path,
    now: datetime,
    *,
    bound: Path | None = None,
    skip: tuple[Path, ...] = (),
) -> int:
    """Attach videos that belong to this fiche. A parent folder does not pull in other shows."""
    from mediatracker.library.identity import file_belongs_to_media

    attached = 0
    if not folder.is_dir() or is_under(folder, skip):
        return 0
    library_root = root.resolve()
    bound_folder = (bound or folder).resolve()
    if bound_folder == library_root:
        return 0
    for path in folder.rglob("*"):
        if not path.is_file() or not is_video(path) or is_under(path, skip):
            continue
        if not file_belongs_to_media(path, library_root, media, bound=bound_folder):
            continue
        parsed = parse_path(path, root=library_root, root_kind=media.type)
        parsed = replace(parsed, media_type=media.type)
        if media.tmdb_id is not None and parsed.tmdb_id in {None, media.tmdb_id}:
            parsed = replace(parsed, tmdb_id=media.tmdb_id)
        item = _ensure_item(db, media, parsed, now, path)
        _cancel_pending(db, item, now)
        attached += 1
    return attached


def _retag_folder(folder: Path, tmdb_id: int, title: str, year: int | None, sanitize) -> Path:
    from mediatracker.core.library_plan import LibraryPlanError, _tag

    if _tag(folder.name) == tmdb_id:
        return folder
    stem = sanitize(title)
    base = f"{stem} ({year})" if year else stem
    dest = folder.parent / f"{base} [tmdbid-{tmdb_id}]"
    if dest.resolve() == folder.resolve():
        return folder
    if dest.exists():
        raise LibraryPlanError("Un dossier porte déjà ce nom.")
    os.replace(folder, dest)
    return dest


def _jellyfin_folder_ids(db: Session, settings: Settings, movies: Path, series: Path) -> dict[str, int]:
    """Map a library folder to the TMDB id Jellyfin already has for a file inside it."""
    from mediatracker.integrations.jellyfin import JellyfinClient, JellyfinError, from_api_item
    from mediatracker.integrations.jellyfin_sync import jellyfin_settings
    from mediatracker.integrations.paths import parse_path_map, translate_path

    url, api_key, user_id, _prefix, path_map = jellyfin_settings(db, settings)
    if not url or not api_key or not user_id:
        return {}
    client = JellyfinClient(url, api_key, user_id)
    try:
        from mediatracker.integrations.jellyfin import resolve_user_id

        user_id = resolve_user_id(client, user_id)
        raw = client.user_items(user_id)
        series_index = client.series_index(user_id) if hasattr(client, "series_index") else {}
    except JellyfinError:
        return {}
    finally:
        client.close()
    mappings = parse_path_map(path_map)
    roots = [path.resolve() for path in (movies, series) if path.is_dir()]
    found: dict[str, int] = {}
    for row in raw:
        watch = from_api_item(row, series_index)
        if watch is None or not watch.path:
            continue
        tmdb_id = watch.series_tmdb_id or watch.tmdb_id
        if tmdb_id is None:
            continue
        translated = translate_path(watch.path, mappings) if mappings else watch.path
        path = Path(translated or watch.path)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        for root in roots:
            try:
                relative = resolved.relative_to(root)
            except ValueError:
                continue
            if not relative.parts:
                continue
            folder = (root / relative.parts[0]).resolve()
            found[str(folder)] = tmdb_id
            break
    return found


def _library_auto_follow(db: Session) -> bool:
    row = db.get(Setting, "library_auto_follow")
    return bool(row is not None and row.value == "true")


def _folder_matches(folder, media: Media) -> bool:
    """TMDB tag, or the same normalized title and year. Jellyfin ids are checked by the caller."""
    if media.tmdb_id is not None and folder.tmdb_id == media.tmdb_id:
        return True
    names = [media.title]
    if media.original_title:
        names.append(media.original_title)
    folded = normalize_title(folder.title)
    if not folded or not any(normalize_title(name) == folded for name in names if name):
        return False
    if media.year is not None and folder.year is not None and media.year != folder.year:
        return False
    return True


def folder_rank(folder, media: Media) -> int:
    """Higher means a closer library folder for the associate picker."""
    if media.tmdb_id is not None and folder.tmdb_id == media.tmdb_id:
        return 100
    if _folder_matches(folder, media):
        return 80
    folded = normalize_title(folder.title)
    names = [normalize_title(name) for name in (media.title, media.original_title or "") if name]
    if folded and any(folded in name or name in folded for name in names if name):
        if media.year is not None and folder.year == media.year:
            return 60
        return 40
    return 0


def match_file(
    db: Session,
    row: LibraryFile,
    *,
    tmdb_id: int,
    media_type: str,
    now: datetime,
    tmdb=None,
) -> LibraryFile:
    if row.status == "missing":
        raise ValueError("MISSING")
    if media_type not in {"film", "series"}:
        raise ValueError("TYPE")
    parsed = ParsedName(
        title=row.parsed_title or "Inconnu",
        year=row.parsed_year,
        season=row.season,
        episode=row.episode,
        tmdb_id=tmdb_id,
        quality=row.quality,
        language=row.language,
        media_type=media_type,
    )
    if parsed.media_type == "series" and (parsed.season is None or parsed.episode is None):
        raise ValueError("EPISODE")
    path = Path(row.path)
    _apply_high(db, row, path, parsed, now, tmdb, forced=True)
    return row


def _ingest(
    db: Session,
    path: Path,
    parsed: ParsedName,
    now: datetime,
    tmdb,
) -> tuple[str, int]:
    row = _upsert_row(db, path, parsed, now)
    from mediatracker.core.tmdb.id_replace import library_file_awaiting_reassociate

    if library_file_awaiting_reassociate(row):
        return _hold(row, "review", "low", row.note or "À réassocier.")
    owned = _owned_by_jellyfin(db, row, path, parsed, now)
    if owned is not None:
        return owned
    if parsed.media_type == "series" and (parsed.season is None or parsed.episode is None):
        return _hold(row, "review", "low", "Numéro de saison ou d'épisode illisible.")
    if parsed.tmdb_id is not None:
        return _apply_high(db, row, path, parsed, now, tmdb, forced=False)
    existing = _local_media(db, parsed)
    if existing is not None:
        parsed = _stamp_local(db, existing, parsed, tmdb)
        _attach(db, row, path, existing, parsed, now)
        row.tmdb_id = existing.tmdb_id or parsed.tmdb_id
        _mark_matched(row, parsed)
        return "matched", 0
    if tmdb is None or not parsed.title or parsed.title == "Inconnu":
        status = "review" if parsed.title and parsed.title != "Inconnu" else "unmatched"
        note = "Pas de jeton TMDB : associez la fiche à la main." if status == "review" else "Titre illisible."
        collision = _collision_note(db, parsed)
        if collision and status == "review":
            note = collision
        return _hold(row, status, "low", note)
    release_sqlite_before_network(db)
    try:
        hits = tmdb.search(parsed.title, parsed.media_type)
    except TmdbError as exc:
        return _hold(row, "review", "low", exc.message)
    outcome = identify(hits, parsed, tmdb=tmdb)
    if outcome.hit is not None:
        exact = outcome.hit
        if _ambiguous_local(db, parsed):
            return _hold(row, "review", "low", "Collision d'année : plusieurs fiches portent ce titre.")
        parsed = replace(parsed, tmdb_id=exact.tmdb_id, year=parsed.year or exact.year)
        return _apply_high(db, row, path, parsed, now, tmdb, forced=False)
    if outcome.pending:
        row.candidates = list(outcome.candidates)
        if hit_years_differ(hits, parsed):
            note = "Collision d'année : plusieurs fiches portent ce titre."
        else:
            note = "Plusieurs fiches TMDB possibles."
        return _hold(row, "review", "low", note)
    collision = _collision_note(db, parsed)
    if collision:
        return _hold(row, "review", "low", collision)
    return _hold(row, "unmatched", "low", "Aucune fiche TMDB.")


def _owned_by_jellyfin(
    db: Session,
    row: LibraryFile,
    path: Path,
    parsed: ParsedName,
    now: datetime,
) -> tuple[str, int] | None:
    """A path Jellyfin already has is not identified again from the filename."""
    from mediatracker.library.jellyfin_align import cached_for_local_path, media_for_cache

    cached = cached_for_local_path(db, path)
    if cached is None:
        return None
    media = media_for_cache(db, cached)
    if media is None:
        return _hold(
            row,
            "review",
            "low",
            "Jellyfin connaît ce fichier. L'alignement n'est pas encore appliqué.",
        )
    stamped = replace(
        parsed,
        tmdb_id=media.tmdb_id,
        media_type=media.type,
        title=media.title or parsed.title,
        year=media.year or parsed.year,
    )
    _attach(db, row, path, media, stamped, now)
    row.tmdb_id = media.tmdb_id
    _mark_matched(row, stamped)
    return "matched", 0


def _apply_high(
    db: Session,
    row: LibraryFile,
    path: Path,
    parsed: ParsedName,
    now: datetime,
    tmdb,
    *,
    forced: bool,
) -> tuple[str, int]:
    fetched: TmdbTitle | None = None
    if tmdb is not None and parsed.tmdb_id is not None:
        release_sqlite_before_network(db)
        try:
            fetched = tmdb.fetch(parsed.tmdb_id, parsed.media_type)
        except TmdbError as exc:
            if not forced and exc.code == "NOT_FOUND":
                return _hold(row, "review", "low", exc.message)
            if exc.code != "NOT_FOUND":
                return _hold(row, "review", "low", exc.message)
    media = _media_by_tmdb(db, parsed.tmdb_id, parsed.media_type) if parsed.tmdb_id else None
    created = 0
    if media is None:
        local = _local_media(db, parsed)
        if local is not None:
            media = local
            _adopt_tmdb(media, parsed, fetched)
    if media is None and parsed.tmdb_id is None and _ambiguous_local(db, parsed):
        return _hold(row, "review", "low", "Collision d'année : plusieurs fiches portent ce titre.")
    if media is None:
        title = fetched.title if fetched is not None else parsed.title
        if not title or title == "Inconnu":
            return _hold(row, "review", "low", "Titre insuffisant pour créer la fiche.")
        from mediatracker.core.tmdb.create import create_from_tmdb

        media, _episodes = create_from_tmdb(
            db,
            media_type=parsed.media_type,
            tmdb_id=parsed.tmdb_id,
            fetched=fetched,
            now=now,
            title=title,
            year=parsed.year,
            followed=_library_auto_follow(db),
            follow_origin="identification",
        )
        created = 1
        if media.followed:
            record_event(
                db, "media.followed", media_id=media.id, payload={"title": media.title, "tmdb_id": media.tmdb_id}
            )
    _attach(db, row, path, media, parsed, now)
    row.tmdb_id = media.tmdb_id or parsed.tmdb_id
    _mark_matched(row, parsed)
    return "matched", created


def _attach(db: Session, row: LibraryFile, path: Path, media: Media, parsed: ParsedName, now: datetime) -> None:
    first: MediaItem | None = None
    for span in _episode_span(parsed):
        item = _ensure_item(db, media, span, now, path)
        if first is None:
            first = item
        _cancel_pending(db, item, now)
    row.media_id = media.id
    row.media_item_id = first.id if first is not None else None
    row.status = "matched"
    row.confidence = "high"
    row.missing_at = None


def _episode_span(parsed: ParsedName) -> list[ParsedName]:
    if (
        parsed.media_type != "series"
        or parsed.season is None
        or parsed.episode is None
        or parsed.episode_end is None
        or parsed.episode_end <= parsed.episode
        or parsed.episode_end - parsed.episode > 30
    ):
        return [parsed]
    return [
        replace(parsed, episode=number, episode_end=None) for number in range(parsed.episode, parsed.episode_end + 1)
    ]


def _mark_matched(row: LibraryFile, parsed: ParsedName) -> None:
    row.candidates = []
    if parsed.episode is not None and parsed.episode_end and parsed.episode_end > parsed.episode:
        row.note = f"Même fichier pour E{parsed.episode:02d}-E{parsed.episode_end:02d}."
    else:
        row.note = None


def _collision_note(db: Session, parsed: ParsedName) -> str | None:
    if not _ambiguous_local(db, parsed):
        return None
    return "Collision d'année : plusieurs fiches portent ce titre."


def _ambiguous_local(db: Session, parsed: ParsedName) -> bool:
    wanted = normalize_title(parsed.title)
    if not wanted or wanted == "inconnu":
        return False
    rows = [
        row
        for row in db.scalars(select(Media).where(Media.type == parsed.media_type)).all()
        if _title_matches(wanted, row) and (parsed.tmdb_id is None or row.tmdb_id in {None, parsed.tmdb_id})
    ]
    if len(rows) < 2:
        return False
    if parsed.year is not None and len([row for row in rows if row.year == parsed.year]) == 1:
        return False
    return len({row.year for row in rows}) > 1 or parsed.year is None


def _ensure_item(db: Session, media: Media, parsed: ParsedName, now: datetime, path: Path) -> MediaItem:
    db.flush()
    season = parsed.season if media.type == "series" else None
    episode = parsed.episode if media.type == "series" else None
    rows = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    item = next((row for row in rows if row.season == season and row.episode == episode), None)
    if item is None:
        item = MediaItem(
            media_id=media.id,
            season=season,
            episode=episode,
            title=None if media.type == "series" else media.title,
            state="downloaded",
        )
        db.add(item)
        db.flush()
    reappear = not item.downloaded or not item.file_path
    item.downloaded = True
    item.downloaded_at = now if reappear else (item.downloaded_at or now)
    item.file_path = _preferred_file(item.file_path, path)
    if item.state in {"missing", "available"}:
        item.state = "downloaded"
    from mediatracker.core.fiche_check import touch_fiche_checked

    touch_fiche_checked(media, now)
    return item


def _preferred_file(current: str | None, path: Path) -> str:
    """Keep the larger copy when the same episode is on disk twice. The scan does not delete either."""
    chosen = str(path.resolve())
    if not current or current == chosen:
        return chosen
    try:
        current_path = Path(current)
        current_size = current_path.stat().st_size if current_path.is_file() else -1
        new_size = path.stat().st_size if path.is_file() else -1
    except OSError:
        return chosen
    if new_size > current_size:
        return chosen
    return current


def _stamp_local(db: Session, media: Media, parsed: ParsedName, tmdb) -> ParsedName:
    """Fill a missing TMDB id from a unique search hit. An existing fiche is never duplicated."""
    if media.tmdb_id is not None or tmdb is None or not parsed.title or parsed.title == "Inconnu":
        return parsed
    release_sqlite_before_network(db)
    try:
        hits = tmdb.search(parsed.title, parsed.media_type)
    except TmdbError:
        return parsed
    hit = exact_hit(hits, parsed)
    if hit is None:
        return parsed
    owner = _media_by_tmdb(db, hit.tmdb_id, parsed.media_type)
    if owner is not None and owner.id != media.id:
        return parsed
    media.tmdb_id = hit.tmdb_id
    if media.year is None and hit.year is not None:
        media.year = hit.year
    return replace(parsed, tmdb_id=hit.tmdb_id, year=parsed.year or hit.year)


def _adopt_tmdb(media: Media, parsed: ParsedName, fetched: TmdbTitle | None) -> None:
    if parsed.tmdb_id is not None and media.tmdb_id is None:
        media.tmdb_id = parsed.tmdb_id
    year = fetched.year if fetched is not None and fetched.year else parsed.year
    if media.year is None and year is not None:
        media.year = year


def _cancel_pending(db: Session, item: MediaItem, now: datetime) -> None:
    rows = db.scalars(
        select(Download).where(Download.media_item_id == item.id, Download.state.in_(_ACTIVE_DOWNLOAD))
    ).all()
    for download in rows:
        if download.handle:
            continue
        download.state = "cancelled"
        download.error_code = "ALREADY_LOCAL"
        download.error = "Déjà présent dans la bibliothèque."
        download.finished_at = now


def _hold(row: LibraryFile, status: str, confidence: str, note: str) -> tuple[str, int]:
    row.status = status
    row.confidence = confidence
    row.note = note
    row.media_id = None
    row.media_item_id = None
    row.missing_at = None
    return status, 0


def _upsert_row(db: Session, path: Path, parsed: ParsedName, now: datetime) -> LibraryFile:
    resolved = str(path.resolve())
    row = db.scalar(select(LibraryFile).where(LibraryFile.path == resolved))
    if row is None:
        row = LibraryFile(path=resolved, seen_at=now)
        db.add(row)
    row.size = path.stat().st_size if path.is_file() else row.size
    row.quality = parsed.quality
    row.language = parsed.language
    row.parsed_title = parsed.title
    row.parsed_year = parsed.year
    row.season = parsed.season
    row.episode = parsed.episode
    row.media_type = parsed.media_type
    if parsed.tmdb_id is not None:
        row.tmdb_id = parsed.tmdb_id
    row.seen_at = now
    row.missing_at = None
    from mediatracker.library.release_name import apply_release_fields, journal_original, remember_original

    if not row.original_name:
        remember_original(row, name=journal_original(db, path) or path.name, source="library")
    apply_release_fields(row, parsed.quality, parsed.language)
    db.flush()
    return row


def _local_media(db: Session, parsed: ParsedName) -> Media | None:
    """One existing fiche of this type. Followed or not: a second fiche is not created."""
    wanted = normalize_title(parsed.title)
    if not wanted or wanted == "inconnu":
        return None
    rows = list(db.scalars(select(Media).where(Media.type == parsed.media_type)).all())
    found = [
        row
        for row in rows
        if _title_matches(wanted, row)
        and (parsed.year is None or row.year is None or row.year == parsed.year)
        and (parsed.tmdb_id is None or row.tmdb_id in {None, parsed.tmdb_id})
    ]
    tagged = [row for row in found if parsed.tmdb_id is not None and row.tmdb_id == parsed.tmdb_id]
    if len(tagged) == 1:
        return tagged[0]
    exact = [row for row in found if parsed.year is not None and row.year == parsed.year]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    if len(found) == 1:
        return found[0]
    return None


def _title_matches(wanted: str, media: Media) -> bool:
    folded = show_key(wanted)
    for name in (media.title, media.original_title):
        if not name:
            continue
        if normalize_title(name) == wanted or (folded and folded == show_key(name)):
            return True
    return False


def _media_by_tmdb(db: Session, tmdb_id: int | None, media_type: str) -> Media | None:
    if tmdb_id is None:
        return None
    return db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type))


def _mark_absent_scoped(
    db: Session,
    seen: set[str],
    roots: tuple[Path, ...],
    now: datetime,
    *,
    scope_roots: list[Path] | None = None,
) -> int:
    return _mark_absent(db, seen, roots, now, scope_roots=scope_roots)


def _mark_absent(
    db: Session,
    seen: set[str],
    roots: tuple[Path, ...],
    now: datetime,
    *,
    scope_roots: list[Path] | None = None,
) -> int:
    existing_roots = [root.resolve() for root in roots if root.is_dir()]
    if not existing_roots:
        return 0
    count = 0
    rows = list(db.scalars(select(LibraryFile).where(LibraryFile.status.not_in(("missing", "trashed")))).all())
    for row in rows:
        if row.path in seen:
            continue
        if row.status == "trashed":
            continue
        path = Path(row.path)
        if scope_roots is not None and not any(_is_under(path, root) for root in scope_roots):
            continue
        if not any(_is_under(path, root) for root in existing_roots):
            continue
        row.status = "missing"
        row.missing_at = now
        count += 1
        if row.media_item_id is None:
            continue
        item = db.get(MediaItem, row.media_item_id)
        if item is None or item.file_path != row.path:
            continue
        other = db.scalar(
            select(LibraryFile).where(
                LibraryFile.media_item_id == item.id,
                LibraryFile.status == "matched",
                LibraryFile.id != row.id,
            )
        )
        if other is not None:
            item.file_path = other.path
            continue
        item.downloaded = False
        item.downloaded_at = None
        item.file_path = None
        if item.state == "downloaded":
            item.state = "missing"
    return count


def _iter_videos(root: Path, skip: tuple[Path, ...] = ()):
    if is_under(root, skip):
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name for name in dirnames if not name.startswith(".") and not is_under(Path(dirpath) / name, skip)
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if is_under(path, skip):
                continue
            if is_video(path):
                yield path


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _root_path(root: Path) -> Path:
    try:
        return root.resolve()
    except OSError:
        return root


def _path_setting(db: Session, key: str) -> Path | None:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return None
    return Path(row.value)


def _setting_int(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def _setting_bool(db: Session, key: str, default: bool) -> bool:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    return row.value == "true"


def _last_scan(db: Session) -> datetime | None:
    row = db.get(Setting, LAST_SCAN_KEY)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _touch_last_scan(db: Session, now: datetime) -> None:
    row = db.get(Setting, LAST_SCAN_KEY)
    if row is None:
        row = Setting(key=LAST_SCAN_KEY, value=now.isoformat(), is_secret=False, updated_at=now)
        db.add(row)
        return
    row.value = now.isoformat()
    row.updated_at = now

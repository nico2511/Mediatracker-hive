"""Worker tick. Orders pause, poll, import, and the start of pending jobs."""

import re
import shutil
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.aria2_unreachable import note_reachable, note_unreachable
from mediatracker.core.disk import enforce_disk_guard
from mediatracker.core.naming import atomic_move, filing_folder, library_destination
from mediatracker.core.pipeline.clients import _debrid, _downloader
from mediatracker.core.pipeline.constants import (
    _FRENCH,
    ACTIVE,
    ARIA_CDN_BURY,
    ARIA_CDN_REUNLOCK,
    BACKOFFS,
    MIN_FREE,
    REDISCOVER,
    REDISCOVER_RESERVED,
    VIDEO_EXT,
    ZERO_PROGRESS_LIMIT,
    french_error,
    host_down,
    logger,
)
from mediatracker.core.pipeline.download_pass_errors import (
    begin_download_pass,
    compose_download_pass_failure,
    reset_pass_error_fields,
    wawacity_had_no_stored_links,
)
from mediatracker.core.pipeline.jobs import (
    _already_local,
    _aware,
    _block_queue,
    _bury,
    _commit,
    _fail,
    _fast_source_poll,
    _hosters,
    _int_setting,
    _item_state,
    _output_name,
    _record_download_source_switch,
    _rediscover,
    _schedule_retry,
    _text_setting,
    _throttle_poll,
    _usable_download_sources,
    advance_download_profile_repli,
    peek_next_download_profile_repli,
)
from mediatracker.core.pipeline.siblings import (
    _heal_obtained_item,
    _mark_obtained,
    _settle_obtained_siblings,
    _sibling_already_obtained,
)
from mediatracker.core.precheck import (
    load_known_files,
    magnet_cache_first,
)
from mediatracker.core.queue_hold import HOLD_CODE, downloads_held, hold_downloads
from mediatracker.core.storage import incomplete_dir, storage_layout
from mediatracker.db.models import Download, LibraryFile, Link, Media, MediaItem, Provider, TrashEntry
from mediatracker.events import record_event, record_library_imported
from mediatracker.providers.base import AUTH_BLOCKED_MESSAGE, DownloadStatus
from mediatracker.providers.downloaders.aria2 import Aria2Error, scrub_url


def tick(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    debrid=None,
    downloader=None,
    disk_free: Callable[[Path], int] | None = None,
    commit: bool = True,
) -> None:
    moment = now or datetime.now(timezone.utc)
    debrid_row, debrid_client = _debrid(db, settings, debrid)
    downloader_row, downloader_client = _downloader(db, settings, downloader)
    held = downloads_held(db)
    if held:
        hold_downloads(db, downloader_client)
    paused = _refresh_pause(db, debrid_row, debrid_client, moment)
    disk_low = enforce_disk_guard(db, settings, downloader_client, moment, disk_free)
    if not held and not disk_low:
        from mediatracker.core.aria2_reconcile import unpause_stale_paused_handles

        unpause_stale_paused_handles(db, settings, downloader=downloader_client, now=moment)
    _poll_active(db, downloader_client, moment, settings)
    _release_stuck(db, moment, downloader_client, allow_stall=not paused and not disk_low and not held)
    db.flush()
    _import_ready(db, moment, settings, downloader_client)
    _settle_obtained_siblings(db, moment, settings)
    from mediatracker.core.debrid_purge import maybe_auto_purge

    maybe_auto_purge(db, settings, debrid_client, moment)
    if not paused and not disk_low and not held:
        _start_pending(
            db,
            settings,
            moment,
            debrid_row,
            debrid_client,
            downloader_row,
            downloader_client,
            disk_free,
        )
    if commit:
        _commit(db)
    else:
        db.flush()


def queue_pause(db: Session) -> tuple[bool, str | None, str | None]:
    row = db.scalar(
        select(Provider).where(
            Provider.kind == "debrid",
            Provider.enabled.is_(True),
            Provider.health_code == "AUTH_BLOCKED",
        )
    )
    if row is not None:
        return True, row.health_code, row.health_message
    if downloads_held(db):
        return True, HOLD_CODE, "Téléchargements en pause."
    return False, None, None


def _forget_aria2_gid(
    downloader,
    gid: str | None,
    *,
    download_id: int | None = None,
    delete_files: bool = False,
) -> None:
    if not gid or downloader is None:
        return
    try:
        downloader.remove(gid, delete_files=delete_files)
    except Exception:
        label = download_id if download_id is not None else gid
        logger.info("Suppression aria2 ignorée pour le téléchargement %s.", label)


def cancel_job(db: Session, row: Download, downloader, *, now: datetime, settings: Settings | None = None) -> None:
    if row.state in {"done", "cancelled"}:
        return
    if row.state == "post_processing":
        raise RuntimeError("POST_PROCESSING")
    _forget_aria2_gid(downloader, row.handle, download_id=row.id, delete_files=True)
    row.state = "cancelled"
    row.finished_at = now
    row.handle = None
    _wake_queue(db, settings)


def retry_job(db: Session, row: Download, settings: Settings) -> None:
    if row.state not in {"failed", "cancelled"}:
        raise RuntimeError("NOT_RETRYABLE")
    now = datetime.now(timezone.utc)
    if _video_still_present(db, settings, row, now):
        if _sibling_already_obtained(db, row):
            _mark_obtained(row, now)
            _heal_obtained_item(db, row.media_item_id)
            return
        row.state = "cancelled"
        row.error_code = "ALREADY_LOCAL"
        row.error = "Déjà présent dans la bibliothèque."
        row.finished_at = now
        return
    row.state = "pending"
    row.attempts = 0
    row.skipped_link_ids = []
    row.error_code = None
    row.error = None
    row.next_retry_at = None
    row.handle = None
    row.progress = 0
    row.done_bytes = 0
    row.speed = None
    row.eta_seconds = None
    row.finished_at = None
    row.attempt_started_at = None
    from mediatracker.core.dead_links import revive_links_for_retry

    revive_links_for_retry(db, row.media_item_id, now)
    _wake_queue(db, settings)


def _refresh_pause(db: Session, row: Provider | None, client, now: datetime) -> bool:
    if row is None or row.health_code != "AUTH_BLOCKED":
        return False
    if client is None or not hasattr(client, "health"):
        return True
    _commit(db)
    health = client.health()
    if health.code == "AUTH_BLOCKED":
        row.health_status = health.status
        row.health_message = health.message or AUTH_BLOCKED_MESSAGE
        row.health_checked_at = now
        return True
    row.health_status = health.status
    row.health_code = health.code
    row.health_message = health.message
    row.health_checked_at = now
    record_event(
        db,
        "provider.health_changed",
        payload={"provider_id": row.id, "type": row.type, "status": health.status, "code": health.code},
    )
    return False


def _poll_active(db: Session, downloader, now: datetime, settings: Settings) -> None:
    if downloader is None:
        return
    rows = db.scalars(select(Download).where(Download.state.in_(("queued", "downloading")))).all()
    saw_success = False
    saw_unreachable = False
    for row in rows:
        if not row.handle:
            continue
        from mediatracker.hive.download_pipeline import is_hive_fetch_download, maybe_refresh_hive_grant

        if is_hive_fetch_download(db, settings, row):
            maybe_refresh_hive_grant(db, settings, row, downloader, now=now, force=False)
        try:
            status = downloader.status(row.handle)
        except Aria2Error as exc:
            if exc.code == "UNREACHABLE":
                saw_unreachable = True
                logger.info("aria2 injoignable, le téléchargement %s garde son identifiant.", row.id)
                continue
            _schedule_retry(db, row, "ARIA2", scrub_url(exc.message)[:300], now)
            continue
        except Exception as exc:
            _schedule_retry(db, row, "ARIA2", scrub_url(str(exc))[:300], now)
            continue
        saw_success = True
        _apply_status(db, row, status, now, downloader, settings=settings)
    if saw_success:
        note_reachable(db, settings, now)
    elif saw_unreachable:
        note_unreachable(db, settings, now)


def _apply_status(
    db: Session,
    row: Download,
    status: DownloadStatus,
    now: datetime,
    downloader,
    *,
    settings: Settings,
) -> None:
    row.done_bytes = status.done_bytes
    row.speed = status.speed
    row.eta_seconds = None
    if status.speed and status.total_bytes and status.total_bytes > status.done_bytes:
        row.eta_seconds = (status.total_bytes - status.done_bytes) // status.speed
    if status.total_bytes:
        row.size = status.total_bytes
        row.progress = min(100, int(status.done_bytes * 100 / status.total_bytes))
    if status.state == "downloading" and row.state != "downloading":
        row.state = "downloading"
        record_event(db, "download.started", item_id=row.media_item_id, payload={"download_id": row.id})
        _item_state(db, row, "downloading")
    elif status.state == "queued":
        row.state = "queued"
    elif status.state == "complete":
        row.progress = 100
        row.state = "post_processing"
        if status.path:
            row.filename = Path(status.path).name
            row.error = status.path
        # ``error`` temporarily holds the local incomplete path, never a debrid URL.
    elif status.state == "error" and _aria_cdn_failure(status.error):
        from mediatracker.hive.download_pipeline import (
            hive_source_transient_failure,
            hive_source_unreachable_message,
            is_hive_fetch_download,
        )

        handle = row.handle
        if is_hive_fetch_download(db, settings, row) and hive_source_transient_failure(status.error):
            _schedule_retry(
                db,
                row,
                "HIVE_SOURCE_UNREACHABLE",
                hive_source_unreachable_message(status.error),
                now,
            )
            if handle:
                _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=False)
        else:
            _schedule_cdn_retry(db, row, status.error or "502", now)
            if handle:
                _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=True)
    elif status.state in {"error", "removed"} and _aria_debrid_expired(status.error):
        handle = row.handle
        _schedule_retry(
            db,
            row,
            "ARIA2_REUNLOCK",
            status.error or "Le lien débridé a expiré.",
            now,
        )
        if handle:
            _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=False)
    elif status.state in {"error", "removed"} and host_down(None, status.error):
        _hand_off(db, row, now, downloader, "LINK_HOST_UNAVAILABLE", status.error or "Hébergeur indisponible.")
    elif status.state in {"error", "removed"}:
        from mediatracker.hive.download_pipeline import (
            hive_auth_failure_message,
            hive_peer_rate_failure,
            is_hive_fetch_download,
            maybe_refresh_hive_grant,
        )

        handle = row.handle
        if is_hive_fetch_download(db, settings, row) and hive_peer_rate_failure(status.error):
            from mediatracker.hive.download_aria2 import hive_failover_after_peer_busy
            from mediatracker.hive.download_pipeline import (
                defer_hive_peer_busy,
                hive_fetch_ctx,
                schedule_hive_immediate_retry,
            )

            ctx = hive_fetch_ctx(db, row)
            switched = False
            if ctx is not None:
                switched = hive_failover_after_peer_busy(db, row, ctx, now)
            if switched:
                schedule_hive_immediate_retry(row, now, clear_handle=True)
            else:
                defer_hive_peer_busy(row, now, clear_handle=True)
            if handle:
                _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=False)
            return
        if is_hive_fetch_download(db, settings, row) and hive_auth_failure_message(status.error):
            if maybe_refresh_hive_grant(db, settings, row, downloader, now=now, force=True):
                resume = getattr(downloader, "resume", None)
                if resume is not None and handle:
                    try:
                        resume(handle)
                    except Exception:
                        pass
                row.state = "downloading"
                row.error_code = None
                row.error = None
                return
        _schedule_retry(db, row, "ARIA2", status.error or "aria2 a échoué.", now)
        if handle:
            _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=False)


def _claim_library_video(db: Session, settings: Settings, media: Media, item: MediaItem, now: datetime) -> None:
    """A video already at the Jellyfin path is downloaded. The job must not start."""
    if item.downloaded:
        return
    from mediatracker.core.library_video import find_episode_on_disk
    from mediatracker.core.storage import library_skip, storage_layout
    from mediatracker.library.scanner import remember_filed

    layout = storage_layout(db, settings)
    found = find_episode_on_disk(
        movies_dir=layout.movies_dir,
        series_dir=layout.series_dir,
        media_type=media.type,
        title=media.title,
        year=media.year,
        season=item.season,
        episode=item.episode,
        tmdb_id=media.tmdb_id,
        skip=library_skip(layout),
    )
    if found is None:
        return
    item.file_path = str(found)
    item.downloaded = True
    item.downloaded_at = item.downloaded_at or now
    if item.state in {"missing", "available", "queued", "downloading", "pending", "failed"}:
        item.state = "downloaded"
    remember_filed(db, found, media, item, now)


def _keep_best(
    db: Session,
    row: Download,
    item: MediaItem,
    media: Media,
    source: Path,
    dest: Path,
    occupant: Path,
    now: datetime,
    settings: Settings,
) -> None:
    """The library already has this episode. Keep the better video and trash the other."""
    from mediatracker.core.library_video import better_video

    link = db.get(Link, row.link_id) if row.link_id else None
    library = _library_row(db, occupant)
    incoming = better_video(
        source,
        occupant,
        challenger_names=_release_names(source, link),
        challenger_qualities=(link.quality if link is not None else None,),
        incumbent_names=_library_names(library, item, occupant),
        incumbent_qualities=_library_qualities(library, item, occupant),
    )
    kept = occupant
    replaced = False
    if incoming:
        placed, replaced = _replace_library_video(db, settings, source, occupant, dest, now, media, item)
        if placed is not None and placed.is_file():
            kept = placed
    else:
        _trash_video(db, settings, source, now, media, item)
    if not kept.is_file():
        _fail(db, row, "FILE_MISSING", "Fichier téléchargé introuvable.", now)
        return
    _finish_download(db, row, item, media, kept, source, now, store_release=replaced)
    _record_hive_received_fingerprint(db, settings, row, item, kept, now)


def _replace_library_video(
    db: Session,
    settings: Settings,
    source: Path,
    occupant: Path,
    dest: Path,
    now: datetime,
    media: Media,
    item: MediaItem,
) -> tuple[Path | None, bool]:
    """Move the library copy to the trash, then file the download at ``dest``.

    The boolean is true only when the download itself landed on ``dest``.
    """
    same = _same_path(source, occupant)
    if not same:
        trashed = _trash_video(db, settings, occupant, now, media, item)
        if trashed is None and occupant.exists():
            _trash_video(db, settings, source, now, media, item)
            return (occupant if occupant.is_file() else None, False)
    if _same_path(source, dest):
        return dest, True
    from mediatracker.core.filing_integrity import check_source_before_move

    if check_source_before_move(db, source, media, item, now):
        return None, False
    try:
        atomic_move(source, dest)
    except OSError as exc:
        logger.info("Le meilleur fichier n'a pas pu remplacer la copie en bibliothèque (%s).", exc)
        if dest.is_file():
            return dest, False
        if occupant.is_file():
            return occupant, False
        return None, False
    return dest, True


def _record_hive_received_fingerprint(
    db: Session,
    settings: Settings,
    row: Download,
    item: MediaItem,
    dest: Path,
    now: datetime,
) -> None:
    from mediatracker.hive.download_pipeline import hive_fetch_ctx, is_hive_fetch_download
    from mediatracker.hive.fingerprint import record_catalog_fingerprint_for_local_file

    if not is_hive_fetch_download(db, settings, row):
        return
    ctx = hive_fetch_ctx(db, row)
    catalog_sha = (ctx.content_sha256_hex or "").strip() if ctx is not None else ""
    if not catalog_sha:
        return
    record_catalog_fingerprint_for_local_file(db, item, dest, sha256_hex=catalog_sha, now=now)


def _finish_download(
    db: Session,
    row: Download,
    item: MediaItem,
    media: Media,
    dest: Path,
    source: Path,
    now: datetime,
    *,
    store_release: bool,
) -> None:
    from mediatracker.core.forget import clear_forget_traces

    item.file_path = str(dest)
    item.downloaded = True
    item.downloaded_at = now
    item.state = "downloaded"
    clear_forget_traces(item)
    link = db.get(Link, row.link_id) if row.link_id else None
    from mediatracker.library.release_name import copy_original, file_source_for_link, remember_original

    if store_release:
        remember_original(
            item,
            name=(link.filename if link is not None and link.filename else None) or source.name,
            source=file_source_for_link(link) if link is not None else "wawacity",
            quality=link.quality if link is not None else None,
            language=link.language if link is not None else None,
        )
    row.state = "done"
    row.error_code = None
    row.error = None
    row.filename = dest.name
    row.finished_at = now
    if _text_setting(db, "downloads_completed_action", "archive") == "remove":
        row.dismissed = True
    row.progress = 100
    record_event(db, "download.completed", media_id=media.id, item_id=item.id, payload={"download_id": row.id})
    from mediatracker.core.film_follow import stop_following_downloaded_film
    from mediatracker.core.series_follow import stop_following_complete_series

    if media.type == "film":
        stop_following_downloaded_film(db, media, reason="downloaded")
    else:
        stop_following_complete_series(db, media, today=now.date())
    record_library_imported(db, media, item, str(dest))
    from mediatracker.integrations.jellyfin_sync import schedule_library_refresh
    from mediatracker.library.scanner import remember_filed

    filed = remember_filed(db, dest, media, item, now)
    copy_original(item, filed)
    schedule_library_refresh(db, now)
    from mediatracker.core.cleanup_classify import request_classify

    request_classify(db)


def _library_row(db: Session, path: Path) -> LibraryFile | None:
    keys = {str(path)}
    try:
        keys.add(str(path.resolve()))
    except OSError:
        pass
    return db.scalar(select(LibraryFile).where(LibraryFile.path.in_(keys)))


def _release_names(source: Path, link: Link | None) -> tuple[str, ...]:
    names = [source.name]
    if link is not None and link.filename:
        names.append(link.filename)
    return tuple(names)


def _on_item(item: MediaItem, occupant: Path) -> bool:
    return bool(item.file_path) and _same_path(Path(item.file_path), occupant)


def _library_names(row: LibraryFile | None, item: MediaItem, occupant: Path) -> tuple[str, ...]:
    names: list[str] = []
    if row is not None and row.original_name:
        names.append(row.original_name)
    if _on_item(item, occupant) and item.original_name:
        names.append(item.original_name)
    return tuple(names)


def _library_qualities(row: LibraryFile | None, item: MediaItem, occupant: Path) -> tuple[str | None, ...]:
    values: list[str | None] = []
    if row is not None:
        values.extend((row.release_quality, row.quality))
    if _on_item(item, occupant):
        values.append(item.release_quality)
    return tuple(values)


def _trash_video(
    db: Session,
    settings: Settings,
    src: Path,
    now: datetime,
    media: Media,
    item: MediaItem,
) -> Path | None:
    """Move one video into the trash. Nothing is unlinked."""
    if not src.is_file():
        return None
    layout = storage_layout(db, settings)
    trash = layout.trash_dir / now.strftime("%Y%m%dT%H%M%S")
    trash.mkdir(parents=True, exist_ok=True)
    dest = trash / src.name
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}-{src.stat().st_ino}{dest.suffix}")
    try:
        original = str(src.resolve())
        size = src.stat().st_size
        atomic_move(src, dest)
    except OSError as exc:
        logger.info("Copie non déplacée vers la corbeille (%s).", exc)
        return None
    days = _int_setting(db, "trash_retention_days", settings.trash_retention_days)
    from mediatracker.core.cleanup import notify_cleanup_done

    entry = TrashEntry(
        original_path=original,
        trash_path=str(dest.resolve()),
        size=size,
        media_id=media.id,
        media_item_id=item.id,
        season=item.season,
        label=(media.title or src.name)[:300],
        reason="duplicate",
        deleted_at=now,
        purge_after=now + timedelta(days=days),
    )
    db.add(entry)
    db.flush()
    notify_cleanup_done(db, media, "episode", item.season, [entry], now, reason="duplicate")
    library = _library_row(db, Path(original))
    if library is not None:
        library.status = "missing"
        library.missing_at = now
    return dest


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


def _import_ready(db: Session, now: datetime, settings: Settings, downloader=None) -> None:
    rows = db.scalars(select(Download).where(Download.state == "post_processing")).all()
    for row in rows:
        # The file is about to leave ``incomplete``. The GID stays "complete" in
        # aria2 unless we drop it, and ``force-save`` puts it back in the session.
        handle = row.handle
        try:
            _import_one(db, row, now, settings)
        finally:
            if handle and row.state != "post_processing" and downloader is not None:
                _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=False)
                if row.handle == handle:
                    row.handle = None


def _import_one(db: Session, row: Download, now: datetime, settings: Settings) -> None:
    item = db.get(MediaItem, row.media_item_id)
    media = db.get(Media, item.media_id) if item is not None else None
    source = Path(row.error) if row.error and row.error.startswith("/") else None
    layout = storage_layout(db, settings)
    if source is None or not source.is_file():
        source = incomplete_dir(layout) / (row.filename or "")
    row.error = None
    if item is None or media is None or not source.is_file():
        _schedule_retry(db, row, "FILE_MISSING", "Fichier téléchargé introuvable.", now)
        return
    if source.stat().st_size <= 0:
        _fail(db, row, "EMPTY_FILE", "Le fichier téléchargé est vide.", now)
        return
    from mediatracker.hive.download_pipeline import is_hive_fetch_download, verify_hive_completed_file

    if is_hive_fetch_download(db, settings, row):
        code, message = verify_hive_completed_file(db, settings, row, source)
        if code:
            _fail(db, row, code, message, now)
            return
    from mediatracker.core.filing_integrity import block_if_suspect

    if block_if_suspect(db, row, item, media, source, now):
        return
    if source.suffix.lower() not in VIDEO_EXT:
        _fail(db, row, "UNSUPPORTED_FILE", "Extension non prise en charge pour Jellyfin.", now)
        return
    from mediatracker.library.identity import release_conflicts

    if media.type == "series" and release_conflicts(source.name, media):
        _fail(
            db,
            row,
            "WRONG_SERIES",
            "Le fichier téléchargé porte le nom d'une autre série. Il n'a pas été déplacé.",
            now,
        )
        return
    library_root = layout.series_dir if media.type == "series" else layout.movies_dir
    # The fiche decides the folder. The release filename, including a
    # Wawacity hoster suffix, is kept on the library row and never used here.
    dest = library_destination(
        movies_dir=layout.movies_dir,
        series_dir=layout.series_dir,
        media_type=media.type,
        title=media.title,
        year=media.year,
        season=item.season,
        episode=item.episode,
        ext=source.suffix,
        tmdb_id=media.tmdb_id,
        library_folder=filing_folder(library_root, title=media.title, year=media.year, tmdb_id=media.tmdb_id),
    )
    from mediatracker.core.library_video import find_library_video, valid_video

    if dest.exists() and not valid_video(dest):
        _fail(db, row, "FILE_EXISTS", "Un fichier porte déjà ce nom dans la bibliothèque.", now)
        return
    occupant = (
        dest
        if valid_video(dest)
        else find_library_video(
            movies_dir=layout.movies_dir,
            series_dir=layout.series_dir,
            media_type=media.type,
            title=media.title,
            year=media.year,
            season=item.season,
            episode=item.episode,
            tmdb_id=media.tmdb_id,
        )
    )
    if occupant is not None:
        _keep_best(db, row, item, media, source, dest, occupant, now, settings)
        return
    try:
        atomic_move(source, dest)
    except OSError as exc:
        code = str(exc)
        if code == "CROSS_DEVICE":
            _fail(db, row, "CROSS_DEVICE", "Le déplacement doit rester sur le même volume.", now)
        else:
            _schedule_retry(db, row, "FILE_MISSING", "Déplacement impossible.", now)
        return
    _finish_download(db, row, item, media, dest, source, now, store_release=True)
    _record_hive_received_fingerprint(db, settings, row, item, dest, now)


def _start_pending(
    db: Session,
    settings: Settings,
    now: datetime,
    debrid_row: Provider | None,
    debrid,
    downloader_row: Provider | None,
    downloader,
    disk_free: Callable[[Path], int] | None,
) -> None:
    if downloader is None:
        return
    limit = _int_setting(db, "max_concurrent_downloads", settings.max_concurrent_downloads)
    active = db.scalars(select(Download).where(Download.state.in_(ACTIVE))).all()
    slots = max(0, limit - len(list(active)))
    if slots == 0:
        return
    pending = [
        row
        for row in db.scalars(
            select(Download).where(Download.state == "pending").order_by(Download.priority.desc(), Download.id)
        ).all()
        if (_aware(row.next_retry_at) is None or _aware(row.next_retry_at) <= now)
    ]
    if not pending:
        return
    preference = _hosters(db, settings)
    _commit(db)
    known, blocked = load_known_files(debrid)
    if blocked:
        _block_queue(db, debrid_row, now)
        return
    started = 0
    for row in pending:
        if started >= slots:
            break
        due = _aware(row.next_retry_at)
        if due is not None and due > now:
            continue
        # Drop the read transaction before AllDebrid. One item, then commit.
        _commit(db)
        outcome = _begin(
            db,
            row,
            now,
            preference,
            debrid_row,
            debrid,
            downloader_row,
            downloader,
            settings,
            disk_free,
            known,
        )
        _commit(db)
        if outcome == "paused":
            break
        if outcome != "started":
            continue
        started += 1


def _begin(
    db,
    row: Download,
    now: datetime,
    preference: list[str],
    debrid_row,
    debrid,
    downloader_row,
    downloader,
    settings: Settings,
    disk_free,
    known: list,
) -> str:
    from mediatracker.core.pipeline.unlock import _from_named_source

    item = db.get(MediaItem, row.media_item_id)
    media = db.get(Media, item.media_id) if item is not None else None
    if item is None or media is None:
        _fail(db, row, "NO_ITEM", "Épisode introuvable.", now)
        return "waiting"
    if media.on_hold and not row.forced:
        return "waiting"
    if not row.forced:
        _claim_library_video(db, settings, media, item, now)
    if not row.forced and _already_local(db, item, now, settings):
        if _sibling_already_obtained(db, row):
            _mark_obtained(row, now)
            return "waiting"
        row.state = "cancelled"
        row.error_code = "ALREADY_LOCAL"
        row.error = "Déjà présent dans la bibliothèque."
        row.finished_at = now
        return "waiting"
    # Magnet cache before any hoster URL is inspected, unlocked, or sent to aria2.
    if magnet_cache_first(db, debrid, item) == "paused":
        _block_queue(db, debrid_row, now)
        row.state = "pending"
        row.error_code = "AUTH_BLOCKED"
        row.error = AUTH_BLOCKED_MESSAGE
        return "paused"
    if row.error_code in (REDISCOVER, REDISCOVER_RESERVED):
        _rediscover(
            db,
            settings,
            media,
            now,
            download=row,
            search_slot_reserved=row.error_code == REDISCOVER_RESERVED,
        )
        row.error_code = None
        row.error = None
        row.skipped_link_ids = []
    from mediatracker.core.precheck import should_bypass_remote_dedup
    from mediatracker.core.remote_search_limit import begin_remote_search, remote_search_limit_phrase

    found = None
    while True:
        reset_pass_error_fields(row)
        begin_download_pass(row)
        effective_known = [] if should_bypass_remote_dedup(row, item) else known
        order = _usable_download_sources(db, media, settings)
        for index, name in enumerate(order):
            defer_final = index < len(order) - 1
            outcome = _from_named_source(
                db,
                name,
                row,
                item,
                media,
                now,
                preference,
                debrid_row,
                debrid,
                settings,
                effective_known,
                defer_final=defer_final,
            )
            if isinstance(outcome, str):
                return outcome
            if outcome is None:
                if index < len(order) - 1:
                    _record_download_source_switch(db, row, media, item, order[index], order[index + 1], now=now)
                continue
            found = outcome
            break
        if found is not None:
            break
        if peek_next_download_profile_repli(db, media, settings, row) is None:
            break
        if not begin_remote_search(media, now, db, settings):
            logger.info(
                "Repli profil ignoré pour le média %s (%s).",
                media.id,
                remote_search_limit_phrase(db, settings),
            )
            break
        if not advance_download_profile_repli(db, media, item, row, settings, now=now):
            break
        _rediscover(db, settings, media, now, download=row, search_slot_reserved=True)
        row.error_code = None
        row.error = None
        row.skipped_link_ids = []
        _commit(db)
    if found is None:
        composed = compose_download_pass_failure(row, order)
        if composed is not None:
            code, message = composed
        else:
            code = row.error_code or "NO_LINK"
            message = row.error or "Aucun lien utilisable."
        if row.state != "failed":
            _fail(db, row, code, french_error(code, message), now)
            if wawacity_had_no_stored_links(row):
                _fast_source_poll(db, media, settings, now)
            else:
                _throttle_poll(db, media, settings, now)
        return "waiting"
    extra_headers: dict[str, str] | None = None
    if isinstance(found, tuple) and len(found) == 6:
        link, direct_url, filename, size, hoster_url, extra_headers = found
    else:
        link, direct_url, filename, size, hoster_url = found
    safe_name = _output_name(filename, link, item, media)
    incomplete = incomplete_dir(storage_layout(db, settings))
    incomplete.mkdir(parents=True, exist_ok=True)
    free = disk_free(incomplete) if disk_free else shutil.disk_usage(incomplete).free
    needed = (size or 0) + 50 * 1024 * 1024 if size else MIN_FREE
    if free < needed:
        row.state = "pending"
        row.error_code = "DISK_FULL"
        row.error = "Espace disque insuffisant."
        row.next_retry_at = now + BACKOFFS[0]
        return "waiting"
    hive_extra: dict[str, str] | None = None
    from mediatracker.hive.download_pipeline import (
        hive_aria2_options,
        is_hive_fetch_download,
        scrub_hive_grant_from_session_file,
    )

    if is_hive_fetch_download(db, settings, row):
        hive_extra = hive_aria2_options(db)
    try:
        if extra_headers is None:
            from mediatracker.hive.download_aria2 import aria2_headers_for_download

            extra_headers = aria2_headers_for_download(db, settings, row, now=now)
        handle = downloader.add(direct_url, incomplete, safe_name, headers=extra_headers, extra_options=hive_extra)
        if is_hive_fetch_download(db, settings, row):
            save = getattr(getattr(downloader, "client", downloader), "save_session", None)
            if save is not None:
                try:
                    save()
                except Exception:
                    pass
            scrub_hive_grant_from_session_file(Path(settings.config_dir) / "aria2.session")
    except Exception as exc:
        _schedule_retry(db, row, "ARIA2", scrub_url(str(exc))[:300], now)
        return "waiting"
    row.handle = handle
    row.filename = safe_name
    row.size = size
    if hoster_url:
        row.debrid_link = hoster_url
    row.state = "queued"
    if row.attempt_started_at is None:
        row.attempt_started_at = now
    row.error_code = None
    row.error = None
    if is_hive_fetch_download(db, settings, row):
        from mediatracker.hive.download_pipeline import clear_hive_peer_busy_strike

        clear_hive_peer_busy_strike(row.id)
    row.debrid_provider_id = debrid_row.id if debrid_row is not None else None
    row.downloader_provider_id = downloader_row.id if downloader_row is not None else None
    record_event(
        db,
        "download.queued",
        media_id=media.id,
        item_id=item.id,
        payload={"download_id": row.id, "filename": safe_name},
    )
    _item_state(db, row, "queued")
    return "started"


def _video_still_present(db: Session, settings: Settings, row: Download, now: datetime) -> bool:
    """Re-stat the library. A stale flag or a sidecar is not a video."""
    item = db.get(MediaItem, row.media_item_id)
    if item is None:
        return False
    media = db.get(Media, item.media_id)
    if media is not None:
        from mediatracker.core.source_poll import refresh_library_claims

        refresh_library_claims(db, media, [item], settings, now)
    from mediatracker.core.presence import local_video

    return local_video(db, settings, item, now) is not None


def schedule_full_retries(db: Session, rows: list[Download], now: datetime, settings: Settings) -> int:
    """Queue a from-scratch retry. Later rows wait out the source delay."""
    delay = _int_setting(db, "source_request_delay_seconds", settings.source_request_delay_seconds)
    gap = timedelta(seconds=max(delay, 5))
    scheduled = 0
    for row in rows:
        if row.state not in {"failed", "cancelled"}:
            continue
        if _video_still_present(db, settings, row, now):
            if _sibling_already_obtained(db, row):
                _mark_obtained(row, now)
                _heal_obtained_item(db, row.media_item_id)
            else:
                row.state = "cancelled"
                row.error_code = "ALREADY_LOCAL"
                row.error = "Déjà présent dans la bibliothèque."
                row.finished_at = now
            scheduled += 1
            continue
        when = now + gap * scheduled
        row.state = "pending"
        row.error_code = REDISCOVER
        row.error = "Nouvelle recherche Wawacity planifiée."
        row.next_retry_at = when
        row.attempts = 0
        row.skipped_link_ids = []
        row.link_id = None
        row.handle = None
        row.progress = 0
        row.done_bytes = 0
        row.speed = None
        row.eta_seconds = None
        row.finished_at = None
        row.attempt_started_at = None
        row.dismissed = False
        from mediatracker.core.dead_links import revive_links_for_retry

        revive_links_for_retry(db, row.media_item_id, now)
        scheduled += 1
    if scheduled:
        _wake_queue(db, settings)
    return scheduled


def _wake_queue(db: Session | None, settings: Settings | None) -> None:
    if db is None:
        return
    from mediatracker.config import get_settings
    from mediatracker.core.worker_wake import REASON_API_QUEUE, wake_worker

    wake_worker(db, settings or get_settings(), REASON_API_QUEUE, commit=False)


def _release_stuck(db: Session, now: datetime, downloader, *, allow_stall: bool) -> None:
    """Drop a host AllDebrid says is down, and any transfer still at 0 bytes."""
    rows = db.scalars(
        select(Download).where(Download.state.in_(("pending", "resolving", "queued", "downloading")))
    ).all()
    for row in rows:
        active = row.state in {"resolving", "queued", "downloading"}
        down = host_down(row.error_code, row.error)
        if down and (active or row.link_id):
            _hand_off(db, row, now, downloader, row.error_code or "LINK_HOST_UNAVAILABLE", row.error)
            continue
        if allow_stall and active and _zero_too_long(row, now):
            _hand_off(db, row, now, downloader, "STALLED", french_error("STALLED", None))


def _zero_too_long(row: Download, now: datetime) -> bool:
    if (row.progress or 0) > 0 or (row.done_bytes or 0) > 0:
        return False
    started = _aware(row.attempt_started_at) or _aware(row.created_at)
    if started is None:
        return False
    return now - started >= ZERO_PROGRESS_LIMIT


def _hand_off(db: Session, row: Download, now: datetime, downloader, code: str, message: str | None) -> None:
    """Forget the current link and let the same tick try the next hoster or Comet."""
    if row.state == "failed":
        return
    handle = row.handle
    row.handle = None
    _forget_aria2_gid(downloader, handle, download_id=row.id, delete_files=True)
    link = db.get(Link, row.link_id) if row.link_id else None
    if link is not None and not link.dead:
        _bury(db, row, link, code, message or code)
    else:
        row.link_id = None
        row.attempts = 0
        row.error_code = code
        row.error = french_error(code, message)
    row.state = "pending"
    row.next_retry_at = now
    row.attempt_started_at = None
    row.progress = 0
    row.done_bytes = 0
    row.speed = None
    row.eta_seconds = None


def _aria_cdn_failure(message: str | None) -> bool:
    """502 and aria2 error 29 are a bad debrid.it link, not a dead hoster."""
    text = (message or "").lower()
    if "502" in text or "download aborted" in text:
        return True
    return bool(re.search(r"(?:^|[^0-9])29(?:[^0-9]|$)", text))


def _aria_debrid_expired(message: str | None) -> bool:
    """Expired or refused debrid CDN URL: aria2 code 3 or HTTP 403/404/410."""
    text = (message or "").lower()
    if re.search(r"\b40[34]\b", text) or re.search(r"\b410\b", text):
        return True
    if "resource not found" in text:
        return True
    return bool(re.search(r"(?:^|[^0-9])3(?:[^0-9]|$)", text))


def _schedule_cdn_retry(db: Session, row: Download, message: str, now: datetime) -> None:
    """Backoff, then a fresh unlock, then the next hoster on the same tick."""
    if row.state == "failed":
        return
    row.attempts += 1
    row.handle = None
    row.state = "pending"
    attempt = row.attempts
    if attempt >= ARIA_CDN_BURY:
        link = db.get(Link, row.link_id) if row.link_id else None
        if link is not None:
            _bury(db, row, link, "ARIA2_CDN", _FRENCH["ARIA2_CDN"])
        else:
            row.error_code = "ARIA2_CDN"
            row.error = _FRENCH["ARIA2_CDN"]
            row.link_id = None
        row.attempts = 0
        row.next_retry_at = now
        logger.info("Lien CDN écarté après des erreurs aria2. job=%s", row.id)
        return
    if attempt >= ARIA_CDN_REUNLOCK:
        row.error_code = "ARIA2_REUNLOCK"
        row.error = "Le lien débridé a échoué (502). Nouveau débridage au prochain essai."
    else:
        row.error_code = "ARIA2_CDN"
        row.error = "Le lien débridé a répondu 502. Nouvel essai après une pause."
    row.next_retry_at = now + BACKOFFS[min(attempt - 1, len(BACKOFFS) - 1)]
    logger.info(
        "aria2 502, nouvel essai. job=%s tentative=%s reunlock=%s",
        row.id,
        attempt,
        attempt >= ARIA_CDN_REUNLOCK,
    )
    del message

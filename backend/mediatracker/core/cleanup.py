"""Move watched files to trash, then purge the trash. Nothing is unlinked in place.

Watched content is moved after the notice delay by default. Followed or not does
not matter. A season is eligible only when every episode is watched. « Garder » on
the fiche, the season, or the episode (including inheritance from above) skips auto
cleanup. A notice goes out before the delete.
"""

import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.cleanup_classify import mark_classify_pass, request_classify, should_run_classify
from mediatracker.core.force_download import IN_PROGRESS
from mediatracker.core.forget import clear_forget_traces, forget_local_file
from mediatracker.core.keep import effective_keep, season_keep_map
from mediatracker.core.library_guard import is_suspended
from mediatracker.core.naming import atomic_move
from mediatracker.core.storage import storage_layout
from mediatracker.db.models import Download, Event, LibraryFile, Media, MediaItem, MediaSeason, Setting, TrashEntry
from mediatracker.events import record_event
from mediatracker.library.scanner import library_roots

logger = logging.getLogger(__name__)

TERMINAL = ("done", "failed", "cancelled")


class CleanupError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass
class PlannedFile:
    item: MediaItem
    path: Path
    size: int


@dataclass
class Plan:
    key: str
    media: Media
    season: int | None
    files: list[PlannedFile]
    eligible_at: datetime
    label: str

    @property
    def nbytes(self) -> int:
        return sum(file.size for file in self.files)


@dataclass
class Classified:
    plan: Plan
    phase: str  # upcoming | due


@dataclass
class DeleteFilesOutcome:
    entries: list[TrashEntry]
    missing: list[str]


_MISSING_MESSAGE = "Fichier déjà absent du disque"


@dataclass(frozen=True)
class FilmCleanup:
    """Why a film that is still on disk is not deleted yet."""

    code: str
    message: str
    watched_at: datetime | None = None
    eligible_at: datetime | None = None
    days: int | None = None
    hours: int | None = None


def trash_root(db: Session, settings: Settings) -> Path:
    return storage_layout(db, settings).trash_dir


def maintenance(db: Session, settings: Settings, *, now: datetime | None = None) -> int:
    """Purge expired trash, warn, then delete what is due. Returns files moved."""
    moment = now or datetime.now(timezone.utc)
    purge_expired(db, settings, moment)
    moved = apply_auto(db, settings, moment)
    record_cleanup_pass(db, settings, moment, moved=moved)
    db.commit()
    return moved


def preview(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> tuple[list[Classified], list[Classified]]:
    """Dry run. ``due`` is what the next pass deletes. ``upcoming`` is only warned."""
    moment = now or datetime.now(timezone.utc)
    due: list[Classified] = []
    upcoming: list[Classified] = []
    for item in classify(db, settings, moment):
        if item.phase == "due":
            due.append(item)
        else:
            upcoming.append(item)
    return due, upcoming


def auto_delete_globally_enabled(db: Session) -> bool:
    """Absence de clé = comportement historique (nettoyage actif si éligible)."""
    row = db.get(Setting, "auto_delete_enabled")
    if row is None or row.value is None or not str(row.value).strip():
        return True
    return str(row.value).strip().lower() in {"1", "true", "yes", "on"}


def apply_auto(db: Session, settings: Settings, now: datetime) -> int:
    if not auto_delete_globally_enabled(db):
        return 0
    if is_suspended(db):
        return 0
    if not should_run_classify(now):
        return 0
    from mediatracker.core.metrics.worker_counters import TASK_CLASSIFY, track_task

    mark_classify_pass(now)
    moved = 0
    with track_task(TASK_CLASSIFY):
        for item in classify(db, settings, now):
            if item.phase == "upcoming":
                _notify_upcoming(db, settings, item.plan, now)
                continue
            try:
                moved += _trash_plan(db, settings, item.plan, now, reason="auto")
            except CleanupError as exc:
                _note_failure(db, item.plan, exc, now)
                continue
            db.commit()
    return moved


def delete_files(
    db: Session,
    settings: Settings,
    *,
    media_id: int,
    scope: str,
    season: int | None,
    item_id: int | None,
    now: datetime | None = None,
) -> DeleteFilesOutcome:
    moment = now or datetime.now(timezone.utc)
    media = db.get(Media, media_id)
    if media is None:
        raise CleanupError("Média introuvable.")
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    if scope == "episode":
        chosen = [item for item in items if item.id == item_id]
        if not chosen:
            raise CleanupError("Épisode introuvable.")
    elif scope == "season":
        if season is None:
            raise CleanupError("La saison est requise.")
        chosen = [item for item in items if item.season == season]
        if not chosen:
            raise CleanupError("Saison introuvable.")
    elif scope == "media":
        chosen = items
    else:
        raise CleanupError("Portée inconnue.")
    files = _owned_files(db, settings, media, chosen, strict=True)
    if not files:
        return DeleteFilesOutcome([], [])
    from mediatracker.core.presence import local_video_in

    layout = storage_layout(db, settings)
    roots = _roots(db, settings)
    trash = trash_root(db, settings)
    to_move: list[PlannedFile] = []
    missing: list[str] = []
    for planned in files:
        claimed = str(planned.path)
        ensure_inside(planned.path, roots, trash)
        found = local_video_in(db, layout, planned.item, moment)
        if found is None:
            missing.append(claimed)
            _record_manual_missing(db, media, planned.item, claimed, moment)
            continue
        size = found.stat().st_size if found.is_file() else planned.size
        to_move.append(PlannedFile(item=planned.item, path=found, size=size))
    entries: list[TrashEntry] = []
    if to_move:
        for planned in to_move:
            ensure_inside(planned.path, roots, trash)
        target_season = season if scope == "season" else None
        entries = _move_all(db, settings, media, to_move, moment, reason="manual", season=target_season)
        notify_cleanup_done(db, media, scope, season, entries, moment, reason="manual")
    if entries or missing:
        _refresh(db, moment)
    if entries:
        request_classify(db, settings)
    db.commit()
    return DeleteFilesOutcome(entries=entries, missing=missing)


def restore(db: Session, settings: Settings, entry_id: int, *, now: datetime | None = None) -> TrashEntry:
    moment = now or datetime.now(timezone.utc)
    entry = db.get(TrashEntry, entry_id)
    if entry is None or entry.purged_at is not None:
        raise CleanupError("Élément de corbeille introuvable.")
    if entry.restored_at is not None:
        raise CleanupError("Cet élément a déjà été restauré.")
    roots = _roots(db, settings)
    trash = trash_root(db, settings)
    src = Path(entry.trash_path)
    dest = Path(entry.original_path)
    ensure_inside(dest, roots, trash)
    if not _is_under(src, trash):
        raise CleanupError("Le fichier de corbeille est en dehors du dossier prévu.")
    if not src.is_file():
        raise CleanupError("Le fichier de corbeille est introuvable.")
    if dest.exists():
        raise CleanupError("Un fichier occupe déjà l'emplacement d'origine.")
    try:
        atomic_move(src, dest)
    except OSError as exc:
        raise CleanupError(_move_message(exc)) from exc
    _prune_empty(src.parent, trash)
    entry.restored_at = moment
    item = db.get(MediaItem, entry.media_item_id) if entry.media_item_id else None
    if item is not None:
        item.downloaded = True
        item.downloaded_at = moment
        item.file_path = str(dest)
        if item.state in {"missing", "available"}:
            item.state = "downloaded"
        clear_forget_traces(item)
    _library_status(db, str(dest), "matched", item.id if item is not None else None)
    request_classify(db, settings)
    db.commit()
    return entry


def purge_expired(db: Session, settings: Settings, now: datetime) -> int:
    trash = trash_root(db, settings)
    rows = list(
        db.scalars(
            select(TrashEntry).where(TrashEntry.restored_at.is_(None), TrashEntry.purged_at.is_(None))
        ).all()
    )
    count = 0
    for row in rows:
        due = row.purge_after
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > now:
            continue
        _unlink_trash(Path(row.trash_path), trash)
        row.purged_at = now
        count += 1
    return count


def purge_one(db: Session, settings: Settings, entry_id: int, *, now: datetime | None = None) -> TrashEntry:
    moment = now or datetime.now(timezone.utc)
    entry = db.get(TrashEntry, entry_id)
    if entry is None or entry.purged_at is not None:
        raise CleanupError("Élément de corbeille introuvable.")
    if entry.restored_at is not None:
        raise CleanupError("Un élément restauré ne se purge pas.")
    trash = trash_root(db, settings)
    freed = entry.size if _trash_file(Path(entry.trash_path), trash) else 0
    _unlink_trash(Path(entry.trash_path), trash)
    entry.purged_at = moment
    _record_purge(db, 1, freed, moment, single_label=entry.label)
    db.commit()
    return entry


def purge_all(db: Session, settings: Settings, *, now: datetime | None = None) -> tuple[int, int]:
    """Unlink every active trash file and close its row. Paths outside the trash stay on disk."""
    moment = now or datetime.now(timezone.utc)
    trash = trash_root(db, settings)
    rows = list(
        db.scalars(
            select(TrashEntry).where(TrashEntry.restored_at.is_(None), TrashEntry.purged_at.is_(None))
        ).all()
    )
    freed = 0
    for row in rows:
        path = Path(row.trash_path)
        if _trash_file(path, trash):
            freed += row.size
        _unlink_trash(path, trash)
        row.purged_at = moment
    if rows:
        _record_purge(db, len(rows), freed, moment, single_label=None)
    db.commit()
    return len(rows), freed


def reclaimed_bytes(db: Session) -> tuple[int, int]:
    """Bytes still in the trash, and bytes already purged."""
    rows = list(db.scalars(select(TrashEntry)).all())
    held = sum(row.size for row in rows if row.purged_at is None and row.restored_at is None)
    purged = sum(row.size for row in rows if row.purged_at is not None)
    return held, purged


def ensure_inside(path: Path, roots: list[Path], trash: Path) -> Path:
    """Resolved path under a media root, never the root itself and never the trash."""
    resolved = path.resolve()
    trash_resolved = trash.resolve()
    if resolved == trash_resolved or _is_under(resolved, trash_resolved):
        raise CleanupError("Ce chemin est dans la corbeille.")
    for root in roots:
        root_resolved = root.resolve()
        if resolved == root_resolved:
            raise CleanupError("Le dossier racine ne peut pas être supprimé.")
        if _is_under(resolved, root_resolved):
            return resolved
    raise CleanupError("Le fichier est en dehors des dossiers de médias.")


def classify(db: Session, settings: Settings, now: datetime) -> list[Classified]:
    notice = timedelta(hours=_int_setting(db, "cleanup_notice_hours", settings.cleanup_notice_hours))
    default_days = _int_setting(db, "auto_delete_days", settings.auto_delete_days)
    found: list[Classified] = []
    medias = list(db.scalars(select(Media).where(Media.keep.is_(False))).all())
    for media in medias:
        for plan in _plans(db, settings, media, default_days):
            phase = _phase(db, plan, now, notice)
            if phase is not None:
                found.append(Classified(plan=plan, phase=phase))
    return found


def _label(media: Media, item: MediaItem) -> str:
    if item.episode is None:
        return media.title
    season = item.season or 0
    episode = item.episode or 0
    return f"{media.title} S{season:02d}E{episode:02d}"


def format_bytes(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} Go".replace(".", ",")
    if size >= 1024**2:
        return f"{size / 1024**2:.1f} Mo".replace(".", ",")
    if size >= 1024:
        return f"{size / 1024:.1f} Ko".replace(".", ",")
    return f"{size} o"


def _items_with_active_download(db: Session, items: list[MediaItem]) -> set[int]:
    ids = [item.id for item in items if item.id is not None]
    if not ids:
        return set()
    return set(
        db.scalars(
            select(Download.media_item_id).where(
                Download.media_item_id.in_(ids),
                Download.state.in_(IN_PROGRESS),
            )
        ).all()
    )


def _owned_for_auto(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    *,
    busy: set[int],
) -> list[PlannedFile]:
    eligible = [item for item in items if item.id not in busy]
    return _owned_files(db, settings, media, eligible)


def _plans(db: Session, settings: Settings, media: Media, default_days: int) -> list[Plan]:
    days = media.auto_delete_days or default_days
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    busy = _items_with_active_download(db, items)
    seasons = season_keep_map(db, media.id)
    if media.type == "film":
        watched = [
            item
            for item in items
            if item.watched and item.watched_at is not None and not effective_keep(media, None, item, seasons)
        ]
        if not watched:
            return []
        latest = max(_aware(item.watched_at) for item in watched if item.watched_at is not None)
        files = _owned_for_auto(db, settings, media, watched, busy=busy)
        if not files:
            return []
        return [
            Plan(
                key=f"media:{media.id}",
                media=media,
                season=None,
                files=files,
                eligible_at=latest + timedelta(days=days),
                label=media.title,
            )
        ]
    season_numbers = {item.season for item in items if item.season is not None}
    plans: list[Plan] = []
    for number in sorted(season_numbers):
        group = [item for item in items if item.season == number]
        if not group or any(not item.watched or item.watched_at is None for item in group):
            continue
        latest = max(_aware(item.watched_at) for item in group if item.watched_at is not None)
        eligible = [
            item
            for item in group
            if not effective_keep(media, number, item, seasons) and item.id not in busy
        ]
        owned = _owned_for_auto(db, settings, media, eligible, busy=busy)
        files = owned
        if not files:
            continue
        plans.append(
            Plan(
                key=f"media:{media.id}:season:{number}",
                media=media,
                season=number,
                files=files,
                eligible_at=latest + timedelta(days=days),
                label=f"{media.title} saison {number}",
            )
        )
    return plans


_PASS_KEY = "cleanup_pass_at"
_MONTHS = (
    "janv.",
    "févr.",
    "mars",
    "avr.",
    "mai",
    "juin",
    "juil.",
    "août",
    "sept.",
    "oct.",
    "nov.",
    "déc.",
)


@dataclass
class OwnedFilesBatch:
    movies: Path
    series: Path
    all_media: list[Media]

    def peers_for(self, media: Media) -> list[Media]:
        return [row for row in self.all_media if row.id != media.id and row.type == media.type]


@dataclass
class MediaExplainContext:
    """Settings and peer rows loaded once per list request (avoids N+1 on ``list_media``)."""

    auto_delete_days: int
    cleanup_notice_hours: int
    notify_timezone: str
    season_maps: dict[int, dict[int, MediaSeason]]
    owned: OwnedFilesBatch
    phase_sent_at: dict[str, datetime | None]

    def season_map_for(self, media_id: int) -> dict[int, MediaSeason]:
        return self.season_maps.get(media_id, {})


def load_owned_files_batch(db: Session, settings: Settings) -> OwnedFilesBatch:
    movies, series = library_roots(db, settings)
    all_media = list(db.scalars(select(Media)).all())
    return OwnedFilesBatch(movies=movies, series=series, all_media=all_media)


def load_season_keep_maps(db: Session, media_ids: list[int]) -> dict[int, dict[int, MediaSeason]]:
    if not media_ids:
        return {}
    grouped: dict[int, dict[int, MediaSeason]] = {media_id: {} for media_id in media_ids}
    rows = db.scalars(select(MediaSeason).where(MediaSeason.media_id.in_(media_ids))).all()
    for row in rows:
        grouped.setdefault(row.media_id, {})[row.number] = row
    return grouped


def load_media_explain_context(db: Session, settings: Settings, media_ids: list[int]) -> MediaExplainContext:
    return MediaExplainContext(
        auto_delete_days=_int_setting(db, "auto_delete_days", settings.auto_delete_days),
        cleanup_notice_hours=_int_setting(db, "cleanup_notice_hours", settings.cleanup_notice_hours),
        notify_timezone=_text_setting(db, "notify_timezone") or settings.notify_timezone,
        season_maps=load_season_keep_maps(db, media_ids),
        owned=load_owned_files_batch(db, settings),
        phase_sent_at={},
    )


def explain_film(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    now: datetime,
    *,
    ctx: MediaExplainContext | None = None,
) -> FilmCleanup | None:
    """Why this film is still on disk. None when it is not a film or has no file."""
    if media.type != "film":
        return None
    present = [item for item in items if item.file_path and item.downloaded]
    if not present:
        return None
    if ctx is not None:
        days = media.auto_delete_days or ctx.auto_delete_days
        hours = ctx.cleanup_notice_hours
        zone = ctx.notify_timezone
    else:
        days = media.auto_delete_days or _int_setting(db, "auto_delete_days", settings.auto_delete_days)
        hours = _int_setting(db, "cleanup_notice_hours", settings.cleanup_notice_hours)
        zone = _text_setting(db, "notify_timezone") or settings.notify_timezone
    watched_at = _latest_watch(present)

    def hold(code: str, message: str, eligible: datetime | None = None) -> FilmCleanup:
        return FilmCleanup(
            code=code,
            message=message,
            watched_at=watched_at,
            eligible_at=eligible,
            days=days,
            hours=hours,
        )

    if media.keep:
        return hold("keep", _with_watch("Marqué « Garder » : exclu du nettoyage automatique.", watched_at, zone))
    watched = [item for item in present if item.watched]
    if not watched:
        return hold(
            "not_watched",
            "Pas marqué comme vu. Le délai ne démarre qu'après un visionnage Jellyfin ou un « vu » manuel.",
        )
    dated = [item for item in watched if item.watched_at is not None]
    if not dated:
        return hold("no_date", f"Marqué vu, sans date de visionnage. Le délai de {days} jours ne peut pas démarrer.")
    seasons = ctx.season_map_for(media.id) if ctx is not None else season_keep_map(db, media.id)
    free = [item for item in dated if not effective_keep(media, None, item, seasons)]
    if not free:
        return hold("item_keep", _with_watch("Fichier marqué « Garder ».", watched_at, zone))
    latest = max(_aware(item.watched_at) for item in free if item.watched_at is not None)
    files = _owned_files(db, settings, media, free, batch=ctx.owned if ctx is not None else None)
    if not files:
        return hold(
            "outside",
            _with_watch(
                "Fichier hors de la bibliothèque, ou dossier correspondant à une autre fiche.",
                latest,
                zone,
            ),
        )
    eligible = latest + timedelta(days=days)
    notice = timedelta(hours=hours)
    plan = Plan(
        key=f"media:{media.id}",
        media=media,
        season=None,
        files=files,
        eligible_at=eligible,
        label=media.title,
    )
    phase = _phase(db, plan, now, notice, sent_cache=ctx.phase_sent_at if ctx is not None else None)
    seen = _day(latest, zone)
    due = _day(eligible, zone)
    sent_at = _notice_sent_at(db, plan, sent_cache=ctx.phase_sent_at if ctx is not None else None)
    if phase is None:
        waiting = (
            f"Vu le {seen}. Suppression au plus tôt le {due} "
            f"({days} jours après ce visionnage), puis préavis de {hours} h."
        )
        return hold("waiting", waiting, eligible)
    if phase == "upcoming":
        due_label, effective = _trash_due_after_notice(eligible, sent_at, notice, hours, zone)
        return hold(
            "upcoming",
            f"Vu le {seen}. Préavis en cours : mise à la corbeille au plus tôt le {due_label}, {hours} h après l'avis.",
            effective,
        )
    return hold(
        "due",
        f"Vu le {seen}. Délai et préavis écoulés : le prochain passage met le fichier à la corbeille.",
        eligible,
    )


def explain_media(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    now: datetime,
    *,
    ctx: MediaExplainContext | None = None,
) -> FilmCleanup | None:
    """Why this fiche still has files. None when nothing is on disk."""
    if media.type == "series":
        return explain_series(db, settings, media, items, now, ctx=ctx)
    return explain_film(db, settings, media, items, now, ctx=ctx)


def explain_series(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    now: datetime,
    *,
    ctx: MediaExplainContext | None = None,
) -> FilmCleanup | None:
    """Why a series still has files. One sentence per season that is on disk."""
    if media.type != "series":
        return None
    present = [item for item in items if item.file_path and item.downloaded and item.season is not None]
    if not present:
        return None
    if ctx is not None:
        days = media.auto_delete_days or ctx.auto_delete_days
        hours = ctx.cleanup_notice_hours
        zone = ctx.notify_timezone
    else:
        days = media.auto_delete_days or _int_setting(db, "auto_delete_days", settings.auto_delete_days)
        hours = _int_setting(db, "cleanup_notice_hours", settings.cleanup_notice_hours)
        zone = _text_setting(db, "notify_timezone") or settings.notify_timezone
    watched_at = _latest_watch(present)

    def hold(code: str, message: str, eligible: datetime | None = None) -> FilmCleanup:
        return FilmCleanup(
            code=code,
            message=message,
            watched_at=watched_at,
            eligible_at=eligible,
            days=days,
            hours=hours,
        )

    if media.keep:
        return hold("keep", _with_watch("Série marquée « Garder ».", watched_at, zone))
    seasons = ctx.season_map_for(media.id) if ctx is not None else season_keep_map(db, media.id)
    notes: list[str] = []
    codes: list[str] = []
    latest_due: datetime | None = None
    for number in sorted({item.season for item in present if item.season is not None}):
        group = [item for item in items if item.season == number]
        note, code, eligible = _season_note(
            db,
            settings,
            media,
            group,
            number,
            seasons,
            days,
            hours,
            zone,
            now,
            owned_batch=ctx.owned if ctx is not None else None,
            phase_cache=ctx.phase_sent_at if ctx is not None else None,
        )
        if note is None or code is None:
            continue
        notes.append(note)
        codes.append(code)
        if eligible is not None:
            latest_due = eligible if latest_due is None else max(latest_due, eligible)
    if not notes:
        return None
    shown = notes[:8]
    extra = len(notes) - len(shown)
    message = " ".join(shown)
    if extra:
        message = f"{message} (+{extra})"
    code = codes[0] if len(set(codes)) == 1 else "mixed"
    return hold(code, message, latest_due)


def _season_note(
    db: Session,
    settings: Settings,
    media: Media,
    group: list[MediaItem],
    number: int,
    seasons: dict[int, MediaSeason],
    days: int,
    hours: int,
    zone: str,
    now: datetime,
    *,
    owned_batch: OwnedFilesBatch | None = None,
    phase_cache: dict[str, datetime | None] | None = None,
) -> tuple[str | None, str | None, datetime | None]:
    season_row = seasons.get(number)
    if season_row is not None and season_row.keep:
        return f"Saison {number} marquée « Garder ».", "season_keep", None
    if any(item.watched and item.watched_at is None for item in group):
        return f"Saison {number} : un épisode est vu sans date.", "no_date", None
    if not group or any(not item.watched for item in group):
        return f"Saison {number} : pas entièrement vue.", "not_watched", None
    free = [item for item in group if not effective_keep(media, number, item, seasons)]
    if not free:
        return f"Saison {number} : fichiers marqués « Garder ».", "item_keep", None
    owned = _owned_files(db, settings, media, free, batch=owned_batch)
    if not owned:
        return f"Saison {number} : fichier hors bibliothèque.", "outside", None
    latest = max(_aware(item.watched_at) for item in free if item.watched_at is not None)
    eligible = latest + timedelta(days=days)
    plan = Plan(
        key=f"media:{media.id}:season:{number}",
        media=media,
        season=number,
        files=owned,
        eligible_at=eligible,
        label=f"{media.title} saison {number}",
    )
    notice = timedelta(hours=hours)
    phase = _phase(db, plan, now, notice, sent_cache=phase_cache)
    seen = _day(latest, zone)
    due = _day(eligible, zone)
    sent_at = _notice_sent_at(db, plan, sent_cache=phase_cache)
    if phase is None:
        return f"Saison {number} : vue le {seen}, suppression au plus tôt le {due}.", "waiting", eligible
    if phase == "upcoming":
        due_label, effective = _trash_due_after_notice(eligible, sent_at, notice, hours, zone)
        return f"Saison {number} : préavis, corbeille au plus tôt le {due_label}.", "upcoming", effective
    return f"Saison {number} : délai écoulé, prochain passage.", "due", eligible


def record_cleanup_pass(db: Session, settings: Settings, now: datetime, *, moved: int) -> None:
    """One journal row per hour: fiches still on disk, and how many files moved."""
    if not _pass_due(db, now):
        return
    medias = list(db.scalars(select(Media)).all())
    grouped: dict[int, list[MediaItem]] = {}
    if medias:
        rows = list(db.scalars(select(MediaItem).where(MediaItem.media_id.in_([media.id for media in medias]))).all())
        for item in rows:
            grouped.setdefault(item.media_id, []).append(item)
    held: list[dict] = []
    for media in medias:
        reason = explain_media(db, settings, media, grouped.get(media.id, []), now)
        if reason is None:
            continue
        held.append(
            {
                "media_id": media.id,
                "title": media.title,
                "code": reason.code,
                "message": reason.message,
                "watched_at": reason.watched_at.isoformat() if reason.watched_at else None,
                "eligible_at": reason.eligible_at.isoformat() if reason.eligible_at else None,
            }
        )
    _remember_pass(db, now)
    if not held and moved == 0:
        return
    record_event(
        db,
        "cleanup.pass",
        level="info",
        when=now,
        payload={
            "moved": moved,
            "held": len(held),
            "films": held[:40],
            "message": _pass_message(held, moved),
            "level": "info",
        },
    )


def _pass_due(db: Session, now: datetime) -> bool:
    row = db.get(Setting, _PASS_KEY)
    if row is None or not row.value:
        return True
    try:
        last = datetime.fromisoformat(row.value)
    except ValueError:
        return True
    return now - _aware(last) >= timedelta(hours=1)


def _remember_pass(db: Session, now: datetime) -> None:
    row = db.get(Setting, _PASS_KEY)
    if row is None:
        db.add(Setting(key=_PASS_KEY, value=now.isoformat(), is_secret=False, updated_at=now))
        return
    row.value = now.isoformat()
    row.updated_at = now


def _pass_message(held: list[dict], moved: int) -> str:
    files = f"{moved} fichier{'' if moved == 1 else 's'} mis à la corbeille"
    if not held:
        return f"Passage du nettoyage : {files}."
    names = ", ".join(f"« {row['title']} »" for row in held[:5])
    extra = f" (+{len(held) - 5})" if len(held) > 5 else ""
    count = f"{len(held)} fiche{'' if len(held) == 1 else 's'}"
    if len({row["code"] for row in held}) == 1:
        return f"Passage du nettoyage : {files}. {count} encore sur le disque ({names}{extra}). {held[0]['message']}"
    return f"Passage du nettoyage : {files}. {count} encore sur le disque : {names}{extra}."


def _latest_watch(items: list[MediaItem]) -> datetime | None:
    dates = [_aware(item.watched_at) for item in items if item.watched and item.watched_at is not None]
    if not dates:
        return None
    return max(dates)


def _with_watch(message: str, watched_at: datetime | None, zone: str) -> str:
    if watched_at is None:
        return message
    return f"{message} Vu le {_day(watched_at, zone)}."


def _day(value: datetime, zone_name: str) -> str:
    from zoneinfo import ZoneInfo

    try:
        zone = ZoneInfo(zone_name or "Europe/Paris")
    except (KeyError, ValueError):
        zone = ZoneInfo("Europe/Paris")
    local = _aware(value).astimezone(zone)
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"


def _text_setting(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _plan_last_appearance(plan: Plan) -> datetime | None:
    """Latest (re)appearance on disk for files in this cleanup plan."""
    dates: list[datetime] = []
    for planned in plan.files:
        watched = planned.item.downloaded_at
        if watched is not None:
            dates.append(_aware(watched))
    if not dates:
        return None
    return max(dates)


def _notice_sent_at(
    db: Session,
    plan: Plan,
    *,
    sent_cache: dict[str, datetime | None] | None = None,
) -> datetime | None:
    """When the cleanup.upcoming notice was sent for this plan, if still valid."""
    appeared_at = _plan_last_appearance(plan)
    dedupe_key = f"cleanup.upcoming:{plan.key}"
    if sent_cache is not None and dedupe_key not in sent_cache:
        sent = db.scalar(
            select(Event.ts).where(Event.type == "cleanup.upcoming", Event.dedupe_key == dedupe_key)
        )
        sent_cache[dedupe_key] = _aware(sent) if sent is not None else None
    if sent_cache is not None:
        sent_at = sent_cache.get(dedupe_key)
    else:
        sent = db.scalar(
            select(Event.ts).where(Event.type == "cleanup.upcoming", Event.dedupe_key == dedupe_key)
        )
        sent_at = _aware(sent) if sent is not None else None
    if sent_at is not None and appeared_at is not None and sent_at < appeared_at:
        return None
    return sent_at


def _trash_due_after_notice(
    eligible: datetime,
    sent_at: datetime | None,
    notice: timedelta,
    hours: int,
    zone: str,
) -> tuple[str, datetime]:
    """Label and instant for earliest trash after the notice period."""
    if sent_at is None:
        return f"dès l'envoi de l'avis + {hours} h", eligible
    effective = max(eligible, sent_at + notice)
    return _day(effective, zone), effective


def _phase(
    db: Session,
    plan: Plan,
    now: datetime,
    notice: timedelta,
    *,
    sent_cache: dict[str, datetime | None] | None = None,
) -> str | None:
    if not plan.files:
        return None
    notify_at = plan.eligible_at - notice
    if now < notify_at:
        return None
    sent_at = _notice_sent_at(db, plan, sent_cache=sent_cache)
    if sent_at is None:
        return "upcoming"
    if now < plan.eligible_at or now < sent_at + notice:
        return "upcoming"
    return "due"


def _record_manual_missing(
    db: Session,
    media: Media,
    item: MediaItem,
    path: str,
    now: datetime,
) -> None:
    record_event(
        db,
        "cleanup.missing",
        level="info",
        media_id=media.id,
        item_id=item.id,
        when=now,
        payload={
            "title": media.title,
            "media_id": media.id,
            "media_type": media.type,
            "season": item.season,
            "episode": item.episode,
            "path": path,
            "message": _MISSING_MESSAGE,
            "level": "info",
        },
    )


def _note_failure(db: Session, plan: Plan, exc: CleanupError, now: datetime) -> None:
    """One journal row per fiche and message. The next fiche of the pass still runs."""
    logger.info("Nettoyage non fait pour %s : %s", plan.label, exc.message)
    code = "volume" if "volume" in exc.message else "move"
    record_event(
        db,
        "cleanup.failed",
        level="warning",
        media_id=plan.media.id,
        dedupe_key=f"cleanup.failed:{plan.key}:{code}",
        when=now,
        payload={
            "title": plan.media.title,
            "media_id": plan.media.id,
            "media_type": plan.media.type,
            "season": plan.season,
            "message": exc.message,
            "level": "warning",
        },
    )


def _notify_upcoming(db: Session, settings: Settings, plan: Plan, now: datetime) -> None:
    del settings
    count = len(plan.files)
    if count == 0:
        return
    hours = _int_setting(db, "cleanup_notice_hours", 24)
    dedupe_key = f"cleanup.upcoming:{plan.key}"
    payload = _notice_payload(plan, hours)
    appeared_at = _plan_last_appearance(plan)
    existing = db.scalar(
        select(Event).where(Event.type == "cleanup.upcoming", Event.dedupe_key == dedupe_key)
    )
    if existing is not None:
        if appeared_at is not None and _aware(existing.ts) < appeared_at:
            existing.ts = now
            existing.payload = payload
            from mediatracker.events.deliver import schedule_deliveries

            schedule_deliveries(db, existing, now)
        return
    record_event(
        db,
        "cleanup.upcoming",
        level="info",
        media_id=plan.media.id,
        dedupe_key=dedupe_key,
        when=now,
        payload=payload,
    )


def notify_cleanup_done(
    db: Session,
    media: Media | None,
    scope: str,
    season: int | None,
    entries: list[TrashEntry],
    now: datetime,
    *,
    reason: str,
) -> None:
    """Journal and notifier row after files were moved to the trash."""
    if not entries:
        return
    stamp = int(now.timestamp())
    target_season = season if scope == "season" else None
    episode = None
    if scope == "episode" and entries[0].media_item_id is not None:
        item = db.get(MediaItem, entries[0].media_item_id)
        if item is not None:
            target_season = item.season
            episode = item.episode
    total = sum(entry.size for entry in entries)
    count = len(entries)
    title = media.title if media is not None else (entries[0].label or "MediaTracker")
    media_id = media.id if media is not None else entries[0].media_id
    record_event(
        db,
        "cleanup.done",
        level="info",
        media_id=media_id,
        dedupe_key=f"cleanup.done:{media_id}:{scope}:{target_season}:{episode}:{stamp}",
        when=now,
        payload={
            "title": title,
            "media_id": media_id,
            "tmdb_id": media.tmdb_id if media is not None else None,
            "poster_url": media.poster_url if media is not None else None,
            "media_type": media.type if media is not None else None,
            "season": target_season,
            "episode": episode,
            "bytes": total,
            "size_label": format_bytes(total),
            "reason": reason,
            "count": count,
            "level": "info",
        },
    )


def _trash_plan(db: Session, settings: Settings, plan: Plan, now: datetime, *, reason: str) -> int:
    roots = _roots(db, settings)
    trash = trash_root(db, settings)
    safe: list[PlannedFile] = []
    for planned in plan.files:
        try:
            ensure_inside(planned.path, roots, trash)
        except CleanupError:
            logger.info("Fichier ignoré hors des dossiers de médias.")
            continue
        safe.append(planned)
    if not safe:
        return 0
    entries = _move_all(db, settings, plan.media, safe, now, reason=reason, season=plan.season)
    notify_cleanup_done(
        db,
        plan.media,
        "season" if plan.season is not None else "media",
        plan.season,
        entries,
        now,
        reason=reason,
    )
    _refresh(db, now)
    return len(entries)


def _move_all(
    db: Session,
    settings: Settings,
    media: Media,
    files: list[PlannedFile],
    now: datetime,
    *,
    reason: str,
    season: int | None,
) -> list[TrashEntry]:
    trash = trash_root(db, settings)
    roots = _roots(db, settings)
    moved: list[tuple[Path, Path]] = []
    prepared: list[tuple[PlannedFile, Path]] = []
    missing: list[PlannedFile] = []
    stamp = now.strftime("%Y%m%dT%H%M%S")
    try:
        for planned in files:
            src = ensure_inside(planned.path, roots, trash)
            if not src.is_file():
                missing.append(planned)
                continue
            dest = trash / stamp / f"{secrets.token_hex(4)}-{src.name}"
            atomic_move(src, dest)
            moved.append((src, dest))
            prepared.append((planned, dest))
            _prune_empty(src.parent, _root_for(src, roots))
    except OSError as exc:
        for src, dest in reversed(moved):
            if dest.is_file() and not src.exists():
                try:
                    atomic_move(dest, src)
                except OSError:
                    pass
        raise CleanupError(_move_message(exc)) from exc
    for planned in missing:
        forget_local_file(db, media, planned.item, now, voluntary=True, update_library=False)
    retention = _int_setting(db, "trash_retention_days", settings.trash_retention_days)
    entries: list[TrashEntry] = []
    seen: set[str] = set()
    for planned, dest in prepared:
        key = str(planned.path.resolve())
        if key in seen:
            forget_local_file(db, media, planned.item, now, voluntary=True, update_library=False)
            continue
        seen.add(key)
        entry = TrashEntry(
            original_path=key,
            trash_path=str(dest.resolve()),
            size=dest.stat().st_size if dest.is_file() else planned.size,
            media_id=media.id,
            media_item_id=planned.item.id,
            season=planned.item.season if season is None else season,
            label=_label(media, planned.item),
            reason=reason,
            deleted_at=now,
            purge_after=now + timedelta(days=retention),
        )
        db.add(entry)
        entries.append(entry)
        forget_local_file(db, media, planned.item, now, voluntary=True, update_library=False)
        _library_status(db, key, "trashed", planned.item.id)
        _cancel_downloads(db, planned.item.id, now)
    return entries


def _owned_files(
    db: Session,
    settings: Settings,
    media: Media,
    items: list[MediaItem],
    *,
    strict: bool = False,
    batch: OwnedFilesBatch | None = None,
) -> list[PlannedFile]:
    """Library files for this fiche. A path that belongs to another series stays put."""
    from mediatracker.library.identity import folder_matches_media, release_conflicts, series_home

    if batch is not None:
        movies, series = batch.movies, batch.series
        others = batch.peers_for(media)
    else:
        movies, series = library_roots(db, settings)
        others = [
            row
            for row in db.scalars(select(Media).where(Media.id != media.id, Media.type == media.type)).all()
        ]
    candidates = _files_for(items)
    owned: list[PlannedFile] = []
    for planned in candidates:
        path = planned.path
        root = series if _inside(path, series) else movies
        if not _inside(path, root):
            continue
        _home, title, year, tag = series_home(path, root)
        if any(folder_matches_media(title, year, tag, row) for row in others):
            continue
        if folder_matches_media(title, year, tag, media) and release_conflicts(path.name, media):
            continue
        owned.append(planned)
    if strict and candidates and not owned:
        raise CleanupError("Ces fichiers sont hors de la bibliothèque ou appartiennent à une autre fiche.")
    return owned


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def _files_for(items: list[MediaItem]) -> list[PlannedFile]:
    found: list[PlannedFile] = []
    seen: set[str] = set()
    for item in items:
        if not item.file_path or not item.downloaded:
            continue
        path = Path(item.file_path)
        key = str(path)
        if key in seen:
            found.append(PlannedFile(item=item, path=path, size=0))
            continue
        seen.add(key)
        size = path.stat().st_size if path.is_file() else 0
        found.append(PlannedFile(item=item, path=path, size=size))
    return found


def _cancel_downloads(db: Session, item_id: int, now: datetime) -> None:
    rows = db.scalars(select(Download).where(Download.media_item_id == item_id, Download.state.not_in(TERMINAL))).all()
    for row in rows:
        row.state = "cancelled"
        row.error_code = "CLEANED"
        row.error = "Fichier retiré : le contenu déjà vu n'est pas retéléchargé."
        row.finished_at = now
        row.handle = None


def _library_status(db: Session, path: str, status: str, item_id: int | None) -> None:
    row = db.scalar(select(LibraryFile).where(LibraryFile.path == path))
    if row is None and item_id is not None:
        row = db.scalar(
            select(LibraryFile).where(LibraryFile.media_item_id == item_id, LibraryFile.status != "missing")
        )
    if row is None:
        return
    row.status = status
    row.missing_at = None
    if status == "matched":
        row.path = path


def _trash_file(path: Path, trash: Path) -> bool:
    """True when this path is a file inside the trash, or already gone from inside it."""
    try:
        resolved = path.resolve()
        trash_resolved = trash.resolve()
    except OSError:
        return False
    if resolved == trash_resolved or not _is_under(resolved, trash_resolved):
        return False
    return resolved.is_file() or not resolved.exists()


def _unlink_trash(path: Path, trash: Path) -> None:
    try:
        resolved = path.resolve()
        trash_resolved = trash.resolve()
    except OSError:
        return
    if resolved == trash_resolved or not _is_under(resolved, trash_resolved):
        return
    if resolved.is_file():
        resolved.unlink()
    _prune_empty(resolved.parent, trash_resolved)


def _record_purge(
    db: Session,
    count: int,
    freed: int,
    moment: datetime,
    *,
    single_label: str | None,
) -> None:
    size_label = format_bytes(freed)
    if single_label:
        event_type = "trash.purged"
        message = f"« {single_label} » retiré de la corbeille ({size_label})."
    else:
        event_type = "trash.emptied"
        noun = "fichier" if count == 1 else "fichiers"
        message = f"Corbeille vidée : {count} {noun}, {size_label} libérés."
    record_event(
        db,
        event_type,
        level="info",
        payload={"count": count, "bytes": freed, "size_label": size_label, "message": message},
        when=moment,
    )


def _prune_empty(start: Path, root: Path) -> None:
    current = start
    root_resolved = root.resolve()
    while current.exists() and current.resolve() != root_resolved and _is_under(current, root_resolved):
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def _refresh(db: Session, now: datetime) -> None:
    from mediatracker.integrations.jellyfin_sync import schedule_library_refresh

    schedule_library_refresh(db, now)


def _notice_payload(plan: Plan, hours: int) -> dict:
    return {
        "title": plan.media.title,
        "media_id": plan.media.id,
        "tmdb_id": plan.media.tmdb_id,
        "poster_url": plan.media.poster_url,
        "media_type": plan.media.type,
        "season": plan.season,
        "bytes": plan.nbytes,
        "size_label": format_bytes(plan.nbytes),
        "hours": hours,
        "count": len(plan.files),
        "level": "info",
    }


def _roots(db: Session, settings: Settings) -> list[Path]:
    movies, series = library_roots(db, settings)
    return [movies, series]


def _root_for(path: Path, roots: list[Path]) -> Path:
    resolved = path.resolve()
    for root in roots:
        if _is_under(resolved, root.resolve()):
            return root
    return roots[0]


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _move_message(exc: OSError) -> str:
    if str(exc) == "CROSS_DEVICE":
        return "Le déplacement vers la corbeille doit rester sur le même volume."
    return "Déplacement vers la corbeille impossible."


def _bool_setting(db: Session, key: str, default: bool) -> bool:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    return row.value == "true"


def _int_setting(db: Session, key: str, default: int) -> int:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value

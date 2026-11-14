"""Detach episodes and files that landed on the wrong fiche. Disk files stay."""

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Download, LibraryFile, Media, MediaItem, MediaSeason, Setting
from mediatracker.integrations.tmdb import TmdbError, TmdbTitle
from mediatracker.library.identity import file_belongs_to_media, media_names, own_series_folder
from mediatracker.library.scanner import _attach_tree, library_roots

REPAIRED_KEY = "library.cross_series_repaired"


class RepairReport:
    def __init__(self) -> None:
        self.detached = 0
        self.removed = 0
        self.restored = 0
        self.attached = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "detached": self.detached,
            "removed": self.removed,
            "restored": self.restored,
            "attached": self.attached,
        }


def repair_media(db: Session, settings: Settings, media: Media, *, fetched: TmdbTitle | None = None) -> RepairReport:
    """Clear foreign links, drop rows that are not this show, then re-read its folder.

    Nothing on disk is deleted. A TMDB refresh overwrites episode titles that
    came from another series folder.
    """
    report = RepairReport()
    movies, series = library_roots(db, settings)
    root = series if media.type == "series" else movies
    now = datetime.now(timezone.utc)
    if fetched is None and media.tmdb_id is not None:
        fetched = _fetch(db, settings, media)

    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    for item in items:
        if not item.file_path:
            continue
        path = Path(item.file_path)
        if file_belongs_to_media(path, root, media):
            continue
        _detach_library_row(db, path, item)
        item.file_path = None
        item.downloaded = False
        item.downloaded_at = None
        if item.state == "downloaded":
            item.state = "missing"
        report.detached += 1

    official: set[tuple[int | None, int | None]] = set()
    if fetched is not None:
        from mediatracker.core.tmdb.apply import apply_tmdb

        apply_tmdb(db, media, fetched, imdb_id=fetched.imdb_id or media.imdb_id, moment=now)
        if media.type == "series":
            official = {
                (episode.season, episode.episode)
                for season in fetched.seasons
                for episode in season.episodes
            }
            report.restored = len(official)

    db.flush()
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    for item in items:
        if _row_belongs(item, root, media, official, fetched is not None):
            continue
        _detach_library_row(db, Path(item.file_path) if item.file_path else None, item)
        for download in db.scalars(select(Download).where(Download.media_item_id == item.id)).all():
            db.delete(download)
        db.delete(item)
        report.removed += 1
    db.flush()

    if fetched is not None:
        from mediatracker.integrations.tmdb_media import fill_next_episode as _fill_next_episode

        remaining = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
        media.next_episode_season = None
        media.next_episode_number = None
        media.next_episode_title = None
        media.next_air_date = None
        _fill_next_episode(media, remaining)

    own = own_series_folder(root, media)
    if own is not None:
        report.attached = _attach_tree(db, media, own, root, now, bound=own)
    return report


def repair_crossed_series(db: Session, settings: Settings) -> int:
    """One startup pass. Later boots leave the library alone; the fiche button can repeat it."""
    if db.get(Setting, REPAIRED_KEY) is not None:
        return 0
    medias = list(db.scalars(select(Media).where(Media.type == "series")).all())
    for media in medias:
        repair_media(db, settings, media)
    now = datetime.now(timezone.utc)
    db.add(Setting(key=REPAIRED_KEY, value="1", is_secret=False, updated_at=now))
    db.commit()
    return len(medias)


def _row_belongs(
    item: MediaItem,
    root: Path,
    media: Media,
    official: set[tuple[int | None, int | None]],
    fetched: bool,
) -> bool:
    if item.file_path and file_belongs_to_media(Path(item.file_path), root, media):
        return True
    if item.tmdb_episode_id is not None:
        return True
    slot = (item.season, item.episode)
    if fetched and slot in official:
        return True
    if not fetched and any(_same(item.title, name) for name in media_names(media)):
        return True
    if fetched:
        return False
    return item.tmdb_episode_id is not None


def _same(title: str | None, name: str) -> bool:
    from mediatracker.core.source_select import titles_match

    return bool(title) and titles_match(title, name)


def apply_tmdb_metadata(db: Session, media: Media, fetched: TmdbTitle) -> list[MediaItem]:
    """Merge TMDB seasons/episodes (or the film row) without duplicating slots."""
    if media.type == "film":
        return [_apply_tmdb_film(db, media, fetched)]
    _apply_tmdb(db, media, fetched)
    db.flush()
    return list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())


def _apply_tmdb_film(db: Session, media: Media, fetched: TmdbTitle) -> MediaItem:
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    film = next((row for row in items if row.season is None and row.episode is None), None)
    if film is None:
        film = MediaItem(
            media_id=media.id,
            title=fetched.title or media.title,
            air_date=fetched.release_date,
            state="missing",
        )
        db.add(film)
    else:
        if fetched.title:
            film.title = fetched.title
        if fetched.release_date is not None:
            film.air_date = fetched.release_date
    db.flush()
    return film


def _apply_tmdb(db: Session, media: Media, fetched: TmdbTitle) -> set[tuple[int | None, int | None]]:
    known_seasons = {
        row.number: row
        for row in db.scalars(select(MediaSeason).where(MediaSeason.media_id == media.id)).all()
    }
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    by_slot = {(row.season, row.episode): row for row in items}
    official: set[tuple[int | None, int | None]] = set()
    for season in fetched.seasons:
        official_season = known_seasons.get(season.number)
        if official_season is None:
            db.add(
                MediaSeason(
                    media_id=media.id,
                    number=season.number,
                    name=season.name,
                    overview=season.overview,
                    air_date=season.air_date,
                    episode_count=season.episode_count,
                    tmdb_id=season.tmdb_id,
                )
            )
        else:
            official_season.name = season.name or official_season.name
            official_season.episode_count = season.episode_count
            official_season.tmdb_id = season.tmdb_id or official_season.tmdb_id
        for episode in season.episodes:
            slot = (episode.season, episode.episode)
            official.add(slot)
            row = by_slot.get(slot)
            if row is None:
                row = MediaItem(
                    media_id=media.id,
                    season=episode.season,
                    episode=episode.episode,
                    title=episode.title,
                    air_date=episode.air_date,
                    still_url=episode.still_url,
                    tmdb_episode_id=episode.tmdb_episode_id,
                    state="missing",
                )
                db.add(row)
                by_slot[slot] = row
            else:
                row.title = episode.title
                row.air_date = episode.air_date
                row.still_url = episode.still_url or row.still_url
                row.tmdb_episode_id = episode.tmdb_episode_id
    if fetched.series_status:
        media.series_status = fetched.series_status
    return official


def _detach_library_row(db: Session, path: Path | None, item: MediaItem) -> None:
    row = None
    if path is not None:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        row = db.scalar(select(LibraryFile).where(LibraryFile.path == resolved))
    if row is None and item.id is not None:
        row = db.scalar(select(LibraryFile).where(LibraryFile.media_item_id == item.id))
    if row is None:
        return
    row.media_id = None
    row.media_item_id = None
    if row.status == "matched":
        row.status = "review"
    row.note = "Détaché : le fichier n'appartient pas à cette fiche."


def _fetch(db: Session, settings: Settings, media: Media) -> TmdbTitle | None:
    from mediatracker.core.tmdb import fetch_title

    if media.tmdb_id is None:
        return None
    try:
        return fetch_title(db, settings, media.tmdb_id, media.type)
    except TmdbError:
        return None

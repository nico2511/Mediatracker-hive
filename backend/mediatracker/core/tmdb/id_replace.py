"""Changement d'identifiant TMDB sur une fiche (confirmation et épisodes)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.db.models import Download, LibraryFile, Media, MediaItem
from mediatracker.integrations.tmdb import TmdbTitle

REASSOCIATE_NOTE_PREFIX = "À réassocier"
REASSOCIATE_NOTE = (
    f"{REASSOCIATE_NOTE_PREFIX} : épisode retiré après changement d'identifiant TMDB "
    "(fichier conservé sur le disque)."
)


def library_file_awaiting_reassociate(row: LibraryFile) -> bool:
    return (
        row.status == "review"
        and row.media_id is None
        and row.media_item_id is None
        and (row.note or "").startswith(REASSOCIATE_NOTE_PREFIX)
    )


def tmdb_id_change_needs_confirm(row: Media, new_tmdb_id: int) -> bool:
    if row.type != "series":
        return False
    if row.tmdb_id is None:
        return False
    return new_tmdb_id != row.tmdb_id


def official_episode_sets(fetched: TmdbTitle) -> tuple[set[tuple[int, int]], set[int]]:
    slots: set[tuple[int, int]] = set()
    episode_ids: set[int] = set()
    for season in fetched.seasons:
        for episode in season.episodes:
            slots.add((episode.season, episode.episode))
            if episode.tmdb_episode_id is not None:
                episode_ids.add(episode.tmdb_episode_id)
    return slots, episode_ids


def item_has_attachment(db: Session, item: MediaItem) -> bool:
    if item.downloaded or item.file_path:
        return True
    if item.id is None:
        return False
    linked = db.scalar(select(LibraryFile.id).where(LibraryFile.media_item_id == item.id).limit(1))
    return linked is not None


def orphan_episode_rows(
    items: list[MediaItem],
    fetched: TmdbTitle,
) -> list[MediaItem]:
    slots, episode_ids = official_episode_sets(fetched)
    orphans: list[MediaItem] = []
    for item in items:
        if item.season is None or item.episode is None:
            continue
        slot = (item.season, item.episode)
        in_slot = slot in slots
        id_ok = item.tmdb_episode_id is None or item.tmdb_episode_id in episode_ids
        if not in_slot or not id_ok:
            orphans.append(item)
    return orphans


def needs_detach_before_rebuild(
    db: Session,
    item: MediaItem,
    official_slots: set[tuple[int, int]],
    official_ids: set[int],
) -> bool:
    if item.season is None or item.episode is None:
        return False
    if not item_has_attachment(db, item):
        return False
    slot = (item.season, item.episode)
    if slot not in official_slots:
        return True
    if item.tmdb_episode_id is not None and item.tmdb_episode_id not in official_ids:
        return True
    return False


def detach_item_files_for_reassociate(db: Session, media: Media, item: MediaItem) -> list[str]:
    """Détache bibliothèque et chemins épisode ; le fichier disque ne bouge pas."""
    paths: list[str] = []
    if item.file_path:
        paths.append(item.file_path)
    for row in list(db.scalars(select(LibraryFile).where(LibraryFile.media_item_id == item.id)).all()):
        paths.append(row.path)
        _mark_library_reassociate(row, media, item)
    for path in paths:
        row = db.scalar(select(LibraryFile).where(LibraryFile.path == path))
        if row is not None and not library_file_awaiting_reassociate(row):
            _mark_library_reassociate(row, media, item)
    if item.file_path:
        path = Path(item.file_path)
        row = db.scalar(select(LibraryFile).where(LibraryFile.path == str(path.resolve())))
        if row is None:
            row = LibraryFile(
                path=str(path.resolve()),
                size=path.stat().st_size if path.is_file() else 0,
                parsed_title=media.title,
                parsed_year=media.year,
                season=item.season,
                episode=item.episode,
                media_type="series",
            )
            db.add(row)
        _mark_library_reassociate(row, media, item)
        paths.append(row.path)
    item.file_path = None
    item.downloaded = False
    item.downloaded_at = None
    if item.state == "downloaded":
        item.state = "missing"
    return paths


def _mark_library_reassociate(row: LibraryFile, media: Media, item: MediaItem) -> None:
    row.media_id = None
    row.media_item_id = None
    row.status = "review"
    row.confidence = "low"
    detail = f"S{item.season}E{item.episode}"
    if item.title:
        detail = f"{detail} — {item.title}"
    row.note = f"{REASSOCIATE_NOTE} Ancienne fiche #{media.id}, {detail}."


@dataclass(frozen=True)
class TmdbIdEpisodePreview:
    item_id: int | None
    season: int | None
    episode: int | None
    title: str | None
    has_file: bool
    file_paths: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class TmdbIdChangePreview:
    previous_tmdb_id: int
    new_tmdb_id: int
    orphans_if_keep: tuple[TmdbIdEpisodePreview, ...]
    removed_if_rebuild: tuple[TmdbIdEpisodePreview, ...]
    files_to_reassociate: tuple[TmdbIdEpisodePreview, ...]

    def as_extra(self) -> dict:
        return {
            "needs_confirm": True,
            "tmdb_id_change": {
                "previous_tmdb_id": self.previous_tmdb_id,
                "new_tmdb_id": self.new_tmdb_id,
                "orphans_if_keep": [p.__dict__ for p in self.orphans_if_keep],
                "removed_if_rebuild": [p.__dict__ for p in self.removed_if_rebuild],
                "files_to_reassociate": [p.__dict__ for p in self.files_to_reassociate],
            },
        }


def _preview_line(db: Session, item: MediaItem, *, reason: str) -> TmdbIdEpisodePreview:
    paths: list[str] = []
    if item.file_path:
        paths.append(item.file_path)
    if item.id is not None:
        for row in db.scalars(select(LibraryFile).where(LibraryFile.media_item_id == item.id)).all():
            if row.path not in paths:
                paths.append(row.path)
    return TmdbIdEpisodePreview(
        item_id=item.id,
        season=item.season,
        episode=item.episode,
        title=item.title,
        has_file=item_has_attachment(db, item),
        file_paths=tuple(paths),
        reason=reason,
    )


def build_tmdb_id_change_preview(
    db: Session,
    row: Media,
    fetched: TmdbTitle,
    *,
    new_tmdb_id: int,
) -> TmdbIdChangePreview:
    assert row.tmdb_id is not None
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == row.id)).all())
    orphans = orphan_episode_rows(items, fetched)
    reason = "hors catalogue TMDB du nouvel identifiant"
    official_slots, official_ids = official_episode_sets(fetched)
    keep_lines = [_preview_line(db, item, reason=reason) for item in orphans]
    remove_lines = list(keep_lines)
    reassoc_lines = [
        _preview_line(db, item, reason="fichier conservé, à réassocier manuellement")
        for item in items
        if needs_detach_before_rebuild(db, item, official_slots, official_ids)
    ]
    return TmdbIdChangePreview(
        previous_tmdb_id=row.tmdb_id,
        new_tmdb_id=new_tmdb_id,
        orphans_if_keep=tuple(keep_lines),
        removed_if_rebuild=tuple(remove_lines),
        files_to_reassociate=tuple(reassoc_lines),
    )


def prepare_rebuild_detach_files(db: Session, media: Media, fetched: TmdbTitle) -> int:
    official_slots, official_ids = official_episode_sets(fetched)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    detached = 0
    for item in items:
        if needs_detach_before_rebuild(db, item, official_slots, official_ids):
            detach_item_files_for_reassociate(db, media, item)
            detached += 1
    return detached


def delete_orphan_episode_rows(db: Session, media: Media, fetched: TmdbTitle) -> int:
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    removed = 0
    for item in orphan_episode_rows(items, fetched):
        detach_item_files_for_reassociate(db, media, item)
        for download in list(db.scalars(select(Download).where(Download.media_item_id == item.id)).all()):
            db.delete(download)
        db.delete(item)
        removed += 1
    return removed


def rebuild_episodes_after_tmdb_id_change(db: Session, media: Media, fetched: TmdbTitle) -> int:
    prepare_rebuild_detach_files(db, media, fetched)
    return delete_orphan_episode_rows(db, media, fetched)

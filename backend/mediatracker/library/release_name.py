"""The release filename, kept after MediaTracker renames the file.

The first name wins. A later Jellyfin rename or an undo must not replace it.
Quality and language are read from that name, not from the cleaned one.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.library.parse import _language, _quality

FILE_SOURCES = frozenset({"wawacity", "comet", "torrentio", "library"})


def release_tokens(filename: str) -> tuple[str | None, str | None]:
    """Resolution and language tokens from a release filename."""
    stem = Path(filename or "").stem
    text = stem or filename or ""
    return _quality(text) or _quality(filename or ""), _language(text) or _language(filename or "")


def file_source_for_link(link: object) -> str:
    kind = str(getattr(link, "kind", "") or "").lower()
    external = str(getattr(link, "external_id", "") or "")
    if external.startswith("torrentio:"):
        return "torrentio"
    if kind in {"magnet", "torrent"} or external.startswith("comet:"):
        return "comet"
    return "wawacity"


def remember_original(
    row: object,
    *,
    name: str | None,
    source: str,
    quality: str | None = None,
    language: str | None = None,
) -> bool:
    """Store the release name once. Returns false when a name is already kept."""
    if getattr(row, "original_name", None):
        return False
    cleaned = Path(name or "").name.strip()
    if not cleaned or cleaned in {".", ".."}:
        return False
    parsed_quality, parsed_language = release_tokens(cleaned)
    row.original_name = cleaned[:500]
    row.file_source = source if source in FILE_SOURCES else "library"
    row.release_quality = parsed_quality or quality
    row.release_language = parsed_language or language
    return True


def copy_original(source_row: object, dest_row: object) -> None:
    if not getattr(source_row, "original_name", None) or getattr(dest_row, "original_name", None):
        return
    dest_row.original_name = source_row.original_name
    dest_row.file_source = source_row.file_source
    dest_row.release_quality = source_row.release_quality
    dest_row.release_language = source_row.release_language


def journal_original(db: Session, path: Path) -> str | None:
    """Release name recorded when this path was a rename source or destination."""
    from mediatracker.db.models import LibraryFile, LibraryRename

    targets = {str(path)}
    try:
        targets.add(str(path.resolve()))
    except OSError:
        pass
    library = db.scalar(select(LibraryFile).where(LibraryFile.path.in_(targets)))
    if library is not None and library.original_name:
        return library.original_name
    for moves in db.scalars(select(LibraryRename.moves)).all():
        if not isinstance(moves, list):
            continue
        for move in moves:
            if not isinstance(move, dict) or not move.get("original_name"):
                continue
            if move.get("dest") in targets or move.get("src") in targets:
                return str(move["original_name"])
    return None


def retarget_path(db: Session, src: Path, dest: Path, *, original: str | None = None) -> None:
    """Point library rows at the new path. The release name stays the first one stored."""
    from mediatracker.db.models import LibraryFile, MediaItem

    targets = {str(src)}
    try:
        targets.add(str(src.resolve()))
    except OSError:
        pass
    kept = original or journal_original(db, src) or src.name
    library = db.scalar(select(LibraryFile).where(LibraryFile.path.in_(targets)))
    if library is not None:
        remember_original(library, name=kept, source=library.file_source or "library")
        library.path = str(dest)
    for item in db.scalars(select(MediaItem).where(MediaItem.file_path.in_(targets))).all():
        remember_original(item, name=kept, source=item.file_source or "library")
        item.file_path = str(dest)


def apply_release_fields(row: object, parsed_quality: str | None, parsed_language: str | None) -> None:
    """Prefer tokens from the kept release name. A cleaned filename must not erase them."""
    original = getattr(row, "original_name", None) or ""
    quality, language = release_tokens(original) if original else (None, None)
    if quality:
        row.quality = quality
    elif parsed_quality:
        row.quality = parsed_quality
    if language:
        row.language = language
    elif parsed_language:
        row.language = parsed_language

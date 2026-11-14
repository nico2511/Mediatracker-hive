"""Whether a video belongs to one fiche.

Episode numbers are not an identity. A file belongs when its series folder
(the first directory under the library root) carries this fiche's TMDB id or
the same title, and the filename does not name another show.
"""

import re
from pathlib import Path

from mediatracker.core.library_plan import _folder_title, _tag
from mediatracker.core.source_select import release_core, titles_match
from mediatracker.db.models import Media
from mediatracker.library.parse import _clean_stem, is_video, season_number

_GENERIC = frozenset(
    {"episode", "video", "file", "download", "movie", "film", "sample", "incomplete", "inconnu"}
)


def media_names(media: Media) -> list[str]:
    return [name for name in (media.title, media.original_title) if name]


def series_home(path: Path, root: Path) -> tuple[Path | None, str | None, int | None, int | None]:
    """Series folder under the library root, with its title, year, and TMDB tag."""
    try:
        relative = path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None, None, None, None
    if len(relative.parts) < 2:
        return None, None, None, None
    folder = (root / relative.parts[0]).resolve()
    title, year = _folder_title(folder.name)
    return folder, title or None, year, _tag(folder.name)


def folder_matches_media(title: str | None, year: int | None, tag: int | None, media: Media) -> bool:
    if tag is not None and media.tmdb_id is not None:
        return tag == media.tmdb_id
    if tag is not None:
        return False
    if not title or not any(titles_match(title, name) for name in media_names(media)):
        return False
    if media.year is not None and year is not None and abs(media.year - year) > 1:
        return False
    return True


def release_conflicts(filename: str, media: Media) -> bool:
    """True when the filename names a different show. Vague names do not count."""
    title = _clean_stem(Path(filename).stem)
    if not title or title.casefold() in _GENERIC:
        return False
    if any(titles_match(title, name) for name in media_names(media)):
        return False
    core = release_core(title)
    if len(core) < 4 or not any(char.isalpha() for char in core):
        return False
    compact = core.replace(" ", "")
    if re.fullmatch(r"[a-f0-9]+", compact):
        return False
    return True


def file_belongs_to_media(path: Path, root: Path, media: Media, *, bound: Path | None = None) -> bool:
    """Series folder plus title or TMDB. A matching episode number is not enough."""
    if release_conflicts(path.name, media):
        return False
    home, title, year, tag = series_home(path, root)
    if home is None:
        loose = _clean_stem(path.stem)
        return bool(loose) and any(titles_match(loose, name) for name in media_names(media))
    if bound is not None and home == bound.resolve() and bound.resolve() != root.resolve():
        if tag is not None and media.tmdb_id is not None and tag != media.tmdb_id:
            return False
        return True
    return folder_matches_media(title, year, tag, media)


def own_series_folder(root: Path, media: Media) -> Path | None:
    if not root.is_dir():
        return None
    named: Path | None = None
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        title, year = _folder_title(child.name)
        tag = _tag(child.name)
        if media.tmdb_id is not None and tag == media.tmdb_id:
            return child.resolve()
        if named is None and folder_matches_media(title, year, tag, media):
            named = child.resolve()
    return named


def show_child_count(folder: Path) -> int:
    """Immediate subfolders that hold videos and are not season directories."""
    if not folder.is_dir():
        return 0
    count = 0
    for child in folder.iterdir():
        if not child.is_dir() or child.name.startswith(".") or season_number(child.name) is not None:
            continue
        if _contains_video(child):
            count += 1
    return count


def _contains_video(folder: Path) -> bool:
    for path in folder.rglob("*"):
        if path.is_file() and is_video(path):
            return True
    return False

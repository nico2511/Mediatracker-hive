"""Jellyfin folder names. The move stays on the same volume."""

import os
import re
from pathlib import Path

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


_TMDB_TAG = re.compile(r"(?i)[\[{(]tmdbid-(\d+)[\]})]")


def sanitize(title: str) -> str:
    cleaned = _UNSAFE.sub(" ", title).strip()
    cleaned = re.sub(r"\s+", " ", cleaned).rstrip(". ")
    return (cleaned or "media")[:150]


def canonical_folder_name(title: str, year: int | None, tmdb_id: int | None) -> str:
    """Jellyfin series folder: ``Title (Year) [tmdbid-N]`` when the fiche has an id."""
    stem = sanitize(title)
    if year:
        stem = f"{stem} ({year})"
    if tmdb_id:
        stem = f"{stem} [tmdbid-{tmdb_id}]"
    return stem


def filing_folder(root: Path, *, title: str, year: int | None, tmdb_id: int | None) -> Path:
    """Directory for this fiche. Never a release or hoster name.

    A folder already tagged with this TMDB id is reused, unless that folder
    itself is still named after a Wawacity release. Otherwise the canonical
    ``Title (Year) [tmdbid-N]`` path is used.
    """
    wanted_name = canonical_folder_name(title, year, tmdb_id)
    wanted = root / wanted_name
    if tmdb_id is None or not root.is_dir():
        return wanted
    tagged = [
        child
        for child in root.iterdir()
        if child.is_dir() and not child.name.startswith(".") and _tmdb_tag(child.name) == tmdb_id
    ]
    exact = [child for child in tagged if child.name == wanted_name]
    if exact:
        return exact[0]
    clean = [child for child in tagged if "wawacity" not in child.name.casefold()]
    if len(clean) == 1:
        return clean[0]
    if wanted.is_dir() or not clean:
        return wanted
    return clean[0]


def library_destination(
    *,
    movies_dir: Path,
    series_dir: Path,
    media_type: str,
    title: str,
    year: int | None,
    season: int | None,
    episode: int | None,
    ext: str,
    tmdb_id: int | None = None,
    library_folder: Path | None = None,
) -> Path:
    file_stem = sanitize(title)
    if year:
        file_stem = f"{file_stem} ({year})"
    suffix = ext if ext.startswith(".") else f".{ext}"
    if media_type == "series":
        season_no = season or 0
        episode_no = episode or 0
        base = library_folder or filing_folder(series_dir, title=title, year=year, tmdb_id=tmdb_id)
        folder = base / f"Season {season_no:02d}"
        return folder / f"{file_stem} - S{season_no:02d}E{episode_no:02d}{suffix.lower()}"
    base = library_folder or filing_folder(movies_dir, title=title, year=year, tmdb_id=tmdb_id)
    return base / f"{file_stem}{suffix.lower()}"


ID_CONFLICT = "Identifiants différents."


def _tmdb_tag(name: str) -> int | None:
    match = _TMDB_TAG.search(name or "")
    if match is None:
        return None
    return int(match.group(1))


def tmdb_ids_in_path(path: Path | str) -> frozenset[int]:
    """Every ``[tmdbid-N]`` carried by a path component."""
    found: set[int] = set()
    for part in Path(path).parts:
        tag = _tmdb_tag(part)
        if tag is not None:
            found.add(tag)
    return frozenset(found)


def different_tmdb_ids(src: Path | str, dest: Path | str) -> str | None:
    """A folder already tagged ``[tmdbid-A]`` must not be filed under ``[tmdbid-B]``."""
    left = tmdb_ids_in_path(src)
    right = tmdb_ids_in_path(dest)
    if left and right and left.isdisjoint(right):
        return ID_CONFLICT
    return None


def atomic_move(src: Path, dest: Path) -> None:
    """Rename on one filesystem. A cross-device copy is refused."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise OSError("FILE_MISSING")
    if src.stat().st_dev != dest.parent.stat().st_dev:
        raise OSError("CROSS_DEVICE")
    os.replace(src, dest)

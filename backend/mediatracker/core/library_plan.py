"""One row per top-level film or series folder, then a Jellyfin rename with undo.

Identification order: a ``[tmdbid-123]`` / ``{tmdb-123}`` tag, then a Jellyfin
item whose path (NFC, via the path map) sits in the folder, then a confident
TMDB search. Ambiguous folders keep suggestions instead of guessing.
"""

import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.core.naming import atomic_move, canonical_folder_name, different_tmdb_ids, sanitize
from mediatracker.core.storage import is_under
from mediatracker.db.models import LibraryRename
from mediatracker.integrations.paths import parse_path_map, translate_path
from mediatracker.library.parse import VIDEO_EXT, is_video, parse_path, strip_release_junk

_TMDB = re.compile(r"(?i)[\[{(]tmdb(?:id)?-(\d+)[\]})]")
_SIDE = frozenset({".srt", ".ass", ".ssa", ".sub", ".idx", ".nfo", ".jpg", ".jpeg", ".png", ".webp"})
_FOLDER_EXTRAS = frozenset({"poster.jpg", "fanart.jpg", "landscape.jpg", "banner.jpg", "tvshow.nfo", "movie.nfo"})


class LibraryPlanError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class FolderFile:
    path: str
    name: str
    season: int | None
    episode: int | None


@dataclass(frozen=True)
class Suggestion:
    tmdb_id: int
    title: str
    year: int | None
    media_type: str
    original_title: str | None = None
    poster_url: str | None = None
    overview: str | None = None
    season_count: int | None = None
    episode_count: int | None = None
    runtime: int | None = None


@dataclass(frozen=True)
class FolderRow:
    path: str
    name: str
    media_type: str
    title: str
    year: int | None
    tmdb_id: int | None
    confidence: str
    source: str
    files: tuple[FolderFile, ...]


def list_folders(movies: Path, series: Path, *, skip: tuple[Path, ...] = ()) -> list[FolderRow]:
    rows: list[FolderRow] = []
    for root, kind in ((movies, "film"), (series, "series")):
        if root is None or not root.is_dir():
            continue
        children = (
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".") and not is_under(path, skip)
        )
        for child in sorted(children, key=lambda item: item.name):
            videos = [
                path
                for path in child.rglob("*")
                if path.is_file() and is_video(path) and not is_under(path, skip)
            ]
            if not videos:
                continue
            title, year = _folder_title(child.name)
            tag = _tag(child.name) or next((_tag(path.name) for path in videos if _tag(path.name)), None)
            files = []
            for path in videos:
                parsed = parse_path(path, root=root, root_kind=kind)
                files.append(
                    FolderFile(
                        path=str(path),
                        name=path.name,
                        season=parsed.season,
                        episode=parsed.episode,
                    )
                )
            files.sort(key=lambda item: (item.season or 0, item.episode or 0, item.name))
            rows.append(
                FolderRow(
                    path=str(child),
                    name=child.name,
                    media_type=kind,
                    title=title,
                    year=year,
                    tmdb_id=tag,
                    confidence="tag" if tag else "none",
                    source="tag" if tag else "",
                    files=tuple(files),
                )
            )
    return rows


def identify_folder(
    folder: Path,
    *,
    media_type: str,
    jellyfin_items: list[dict] | None = None,
    path_map: str | None = None,
    tmdb=None,
) -> tuple[int | None, str, str, list[Suggestion]]:
    """Return tmdb id, confidence (tag|jellyfin|tmdb|ambiguous|none), source label, suggestions."""
    tag = _tag(folder.name)
    if tag is None:
        for path in folder.rglob("*"):
            if path.is_file():
                tag = _tag(path.name)
                if tag is not None:
                    break
    if tag is not None:
        return tag, "tag", "Étiquette du dossier", []
    found = _jellyfin_ids(folder, jellyfin_items or [], path_map)
    if len(found) == 1:
        return found[0], "jellyfin", "Jellyfin", []
    if len(found) > 1:
        return None, "ambiguous", "Jellyfin", [
            Suggestion(tmdb_id=item, title="", year=None, media_type=media_type) for item in sorted(found)
        ]
    title, year = _folder_title(folder.name)
    if tmdb is None or not title:
        return None, "none", "", []
    from mediatracker.core.tmdb.identify import identify
    from mediatracker.library.parse import ParsedName

    try:
        hits = tmdb.search(title, media_type)
    except Exception:
        return None, "none", "", []
    parsed = ParsedName(
        title=title,
        year=year,
        season=None,
        episode=None,
        tmdb_id=None,
        quality=None,
        language=None,
        media_type=media_type,
    )
    result = identify(hits, parsed, tmdb=tmdb, with_glance=False)
    if result.hit is not None:
        return result.hit.tmdb_id, "tmdb", "TMDB", []
    if result.pending:
        suggestions = [_suggestion(hit, media_type) for hit in hits[:5]]
        return None, "ambiguous", "TMDB", suggestions
    return None, "none", "", []


def rename_folder(  # noqa: PLR0913
    db: Session,
    folder: Path,
    *,
    root: Path,
    media_type: str,
    title: str,
    year: int | None,
    tmdb_id: int | None = None,
    now: datetime | None = None,
) -> LibraryRename | None:
    """Move the folder to the Jellyfin layout. Subtitles, nfo and images follow."""
    moment = now or datetime.now(timezone.utc)
    folder = folder.resolve()
    root = root.resolve()
    if not _is_inside(folder, root) or folder == root:
        raise LibraryPlanError("Le dossier est en dehors de la bibliothèque.")
    moves = _plan(folder, root, media_type, title, year, tmdb_id)
    if not moves:
        return None
    if any(different_tmdb_ids(src, dest) for src, dest in moves):
        raise LibraryPlanError("Identifiants différents.")
    if any(_other_series(folder, root, dest) for _src, dest in moves):
        raise LibraryPlanError(
            "Le dossier de destination existe déjà. Aucun fichier n'est déplacé vers une autre série."
        )
    for src, dest in moves:
        if dest.exists() and src.resolve() != dest.resolve():
            raise LibraryPlanError(f"Un fichier occupe déjà {dest.name}.")
        parent = dest.parent if dest.parent.exists() else root
        if src.stat().st_dev != parent.stat().st_dev:
            raise LibraryPlanError("Le déplacement doit rester sur le même volume.")
    applied: list[dict[str, str]] = []
    try:
        for src, dest in moves:
            if src.resolve() == dest.resolve():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                continue
            atomic_move(src, dest)
            from mediatracker.library.release_name import journal_original, retarget_path

            original = journal_original(db, src) or src.name
            retarget_path(db, src, dest, original=original)
            applied.append({"src": str(src), "dest": str(dest), "original_name": original})
    except OSError as exc:
        _rollback_paths(applied)
        raise LibraryPlanError("Déplacement impossible.") from exc
    _prune_empty(folder, root)
    row = LibraryRename(created_at=moment, moves=applied)
    db.add(row)
    db.flush()
    return row


def undo_rename(db: Session, rename_id: int, *, now: datetime | None = None) -> LibraryRename:
    moment = now or datetime.now(timezone.utc)
    row = db.get(LibraryRename, rename_id)
    if row is None or row.undone_at is not None:
        raise LibraryPlanError("Ce renommage ne peut pas être annulé.")
    pairs = [(Path(item["dest"]), Path(item["src"])) for item in reversed(row.moves or []) if isinstance(item, dict)]
    for src, dest in pairs:
        if dest.exists() and src.resolve() != dest.resolve():
            raise LibraryPlanError(f"Impossible d'annuler : {dest.name} existe déjà.")
    for src, dest in pairs:
        if not src.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        from mediatracker.library.release_name import retarget_path

        if src.is_dir():
            os.replace(src, dest)
        elif src.is_file():
            atomic_move(src, dest)
            retarget_path(db, src, dest)
    row.undone_at = moment
    return row


def _plan(
    folder: Path,
    root: Path,
    media_type: str,
    title: str,
    year: int | None,
    tmdb_id: int | None = None,
) -> list[tuple[Path, Path]]:
    file_stem = sanitize(title)
    folder_name = canonical_folder_name(title, year, tmdb_id)
    dest_root = root / folder_name
    videos = [path for path in folder.rglob("*") if path.is_file() and is_video(path)]
    planned: list[tuple[Path, Path]] = []
    used: set[Path] = set()
    for video in videos:
        parsed = parse_path(video, root=root, root_kind=media_type)
        if media_type == "series" and parsed.season is not None and parsed.episode is not None:
            name = f"{file_stem} S{parsed.season:02d}E{parsed.episode:02d}{video.suffix.lower()}"
            dest = dest_root / f"Season {parsed.season:02d}" / name
        else:
            dest = dest_root / f"{file_stem}{video.suffix.lower()}" if media_type == "film" else dest_root / video.name
        planned.append((video, dest))
        used.add(video)
        for extra in _companions(video):
            if extra in used:
                continue
            suffix = extra.name[len(video.stem) :]
            planned.append((extra, dest.parent / f"{dest.stem}{suffix}"))
            used.add(extra)
    for extra_name in _FOLDER_EXTRAS:
        extra = folder / extra_name
        if extra.is_file() and extra not in used:
            planned.append((extra, dest_root / extra.name))
    return [(src, dest) for src, dest in planned if src.resolve() != dest.resolve()]


def _companions(video: Path) -> list[Path]:
    found: list[Path] = []
    if not video.parent.is_dir():
        return found
    for path in video.parent.iterdir():
        if not path.is_file() or path == video:
            continue
        if path.suffix.lower() in _SIDE and (path.stem == video.stem or path.name.startswith(video.stem + ".")):
            found.append(path)
    return found


def _jellyfin_ids(folder: Path, items: list[dict], path_map: str | None) -> list[int]:
    mappings = parse_path_map(path_map)
    folder_key = _nfc(str(folder)).rstrip("/")
    found: set[int] = set()
    for item in items:
        raw = item.get("Path") if isinstance(item, dict) else None
        if not isinstance(raw, str) or not raw:
            continue
        translated = translate_path(raw, mappings) if mappings else raw
        key = _nfc(translated or raw).rstrip("/")
        if key != folder_key and not key.startswith(folder_key + "/"):
            continue
        providers = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
        tmdb = providers.get("Tmdb") or providers.get("TMDB")
        if isinstance(tmdb, int):
            found.add(tmdb)
        elif isinstance(tmdb, str) and tmdb.isdigit():
            found.add(int(tmdb))
    return sorted(found)


def _suggestion(hit, media_type: str) -> Suggestion:
    overview = getattr(hit, "overview", None)
    if isinstance(overview, str):
        overview = overview.strip()
        if len(overview) > 180:
            overview = overview[:177].rstrip() + "…"
    poster = getattr(hit, "poster_url", None)
    if isinstance(poster, str) and "/t/p/w500/" in poster:
        poster = poster.replace("/t/p/w500/", "/t/p/w92/")
    return Suggestion(
        tmdb_id=hit.tmdb_id,
        title=hit.title,
        year=hit.year,
        media_type=getattr(hit, "media_type", None) or media_type,
        original_title=getattr(hit, "original_title", None),
        poster_url=poster if isinstance(poster, str) and poster else None,
        overview=overview or None,
        season_count=getattr(hit, "season_count", None),
        episode_count=getattr(hit, "episode_count", None),
        runtime=getattr(hit, "runtime", None),
    )


def _folder_title(name: str) -> tuple[str, int | None]:
    cleaned = _TMDB.sub(" ", name)
    match = re.match(r"^(?P<title>.+?)(?: \((?P<year>(?:19|20)\d{2})\))?$", cleaned.strip())
    if match is None:
        return cleaned.strip() or name, None
    title = strip_release_junk(match.group("title").strip()) or match.group("title").strip() or name
    year = int(match.group("year")) if match.group("year") else None
    return title, year


def _tag(name: str) -> int | None:
    match = _TMDB.search(name or "")
    if match is None:
        return None
    return int(match.group(1))


def _nfc(value: str) -> str:
    text = unicodedata.normalize("NFC", value.replace("\\", "/"))
    while "//" in text:
        text = text.replace("//", "/")
    return text


def _other_series(folder: Path, root: Path, dest: Path) -> bool:
    """True when the destination is an existing series folder other than the source."""
    try:
        src_top = root / folder.relative_to(root).parts[0]
        dest_top = root / dest.relative_to(root).parts[0]
    except (ValueError, IndexError):
        return True
    if not dest_top.exists():
        return False
    return dest_top.resolve() != src_top.resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _rollback_paths(applied: list[dict[str, str]]) -> None:
    for item in reversed(applied):
        src = Path(item["dest"])
        dest = Path(item["src"])
        if src.is_file() and not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)


_JUNK_FILES = frozenset({"thumbs.db", ".ds_store", "desktop.ini"})
_ORPHAN_SUFFIXES = frozenset({".nfo", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".tbn"})
_THUMB_TAILS = ("-thumb.jpg", "-thumb.jpeg", "-thumb.png", "-thumb.webp")
_FOLDER_NFO = re.compile(r"(?i)^(tvshow|movie|season|season\d+|season-specials)\.nfo$")
# Names that are not dot-prefixed but are still system directories.
_SYSTEM_DIRS = frozenset({"@eadir", "lost+found", "#recycle"})
# Files Syncthing (and similar tools) keep next to the library. A folder that
# holds one of them is not empty, even when it has no video.
_MARKER_FILES = frozenset({".stignore", ".stfolder", ".stversions"})
_MARKER_MESSAGE = (
    "Ce dossier est caché ou c'est un marqueur système "
    "(.stfolder, .stversions, .stignore, @eaDir, .Trash, lost+found, #recycle, .mediatracker). "
    "Il ne sera pas supprimé."
)


@dataclass(frozen=True)
class EmptyFolderPreview:
    path: str
    file_count: int
    size: int
    summary: str


@dataclass(frozen=True)
class TrashedFile:
    original: str
    trash_path: str
    size: int


def preview_empty_folders(*roots: Path, protected: tuple[Path, ...] = ()) -> list[EmptyFolderPreview]:
    """Effectively empty folders, plus leftover thumbs and nfo files.

    A folder with no video is effectively empty when every file is artwork,
    metadata, or junk (posters, ``logo.svg``, ``-thumb.jpg``, ``.nfo``). A
    folder that contains any video is never proposed. A ``-thumb`` or ``.nfo``
    whose matching video is gone is proposed on its own. Roots, ancestors,
    hidden directories, and system markers stay out of the list.
    """
    search = _real_dirs(roots)
    forbidden = _forbidden_paths(search, protected)
    markers = _collect_markers(search)
    folders: list[EmptyFolderPreview] = []
    empty_dirs: list[Path] = []
    for root in search:
        for current, dirnames, _filenames in os.walk(root, topdown=True, followlinks=False):
            path = _real(Path(current))
            dirnames[:] = [name for name in dirnames if not _skipped_dir_name(name)]
            if not _cleanable_dir(path, root, search, forbidden, markers):
                continue
            if not _effectively_empty(path):
                continue
            count, size, summary = _describe(path)
            folders.append(EmptyFolderPreview(str(path), count, size, summary))
            empty_dirs.append(path)
    orphans: list[EmptyFolderPreview] = []
    for root in search:
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            path = _real(Path(current))
            dirnames[:] = [name for name in dirnames if not _skipped_dir_name(name)]
            if not _cleanable_dir(path, root, search, forbidden, markers) or path is None:
                continue
            if _under_any(path, empty_dirs):
                continue
            for name in filenames:
                file_path = Path(current) / name
                if not _is_orphan_sidecar(file_path):
                    continue
                resolved = _real(file_path)
                if resolved is None:
                    continue
                try:
                    size = file_path.stat().st_size
                except OSError:
                    size = 0
                orphans.append(
                    EmptyFolderPreview(
                        str(resolved),
                        1,
                        size,
                        "Orphelin · la vidéo correspondante n'existe plus",
                    )
                )
    found = folders + orphans
    found.sort(key=lambda item: len(item.path), reverse=True)
    return found


def remove_empty_folders(
    paths: list[str],
    *roots: Path,
    protected: tuple[Path, ...] = (),
    trash: Path | None = None,
    moved: list[TrashedFile] | None = None,
) -> list[str]:
    """Move previewed folders and orphan sidecars into the trash.

    A root, an ancestor, a hidden dir, or a marker is an error. A folder that
    contains a video is left untouched. Files are renamed into the trash and
    never unlinked.
    """
    search = _real_dirs(roots)
    forbidden = _forbidden_paths(search, protected)
    markers = _collect_markers(search)
    destination = _ensure_trash(trash, search)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    ordered = sorted((Path(item) for item in paths), key=lambda item: len(item.parts), reverse=True)
    accepted: list[Path] = []
    for path in ordered:
        resolved = _real(path)
        if resolved is None or resolved in forbidden or not _strict_child(resolved, search):
            raise LibraryPlanError(
                "Ce chemin est une racine de bibliothèque, un dossier parent, ou hors des bibliothèques."
            )
        if _relative_is_protected(resolved, search) or _holds_marker(resolved, markers):
            raise LibraryPlanError(_MARKER_MESSAGE)
        accepted.append(resolved)
    removed: list[str] = []
    for resolved in accepted:
        if resolved.is_file():
            if not _is_orphan_sidecar(resolved):
                continue
            item = _move_one(resolved, destination, stamp)
            if item is None:
                continue
            if moved is not None:
                moved.append(item)
            removed.append(str(resolved))
            continue
        if not _effectively_empty(resolved):
            continue
        files = [child for child in resolved.rglob("*") if child.is_file() and not child.is_symlink()]
        held: list[TrashedFile] = []
        failed = False
        for child in files:
            item = _move_one(child, destination, stamp)
            if item is None:
                failed = True
                break
            held.append(item)
        if moved is not None:
            moved.extend(held)
        if failed:
            continue
        _remove_empty_dirs(resolved)
        removed.append(str(resolved))
    return removed


def _real(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve()
    except OSError:
        return None


def _real_dirs(paths: tuple[Path, ...]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        if path is None:
            continue
        resolved = _real(path)
        if resolved is not None and resolved.is_dir() and resolved not in found:
            found.append(resolved)
    return found


def _forbidden_paths(roots: list[Path], extra: tuple[Path, ...]) -> set[Path]:
    """Roots, their ancestors, and the other configured app directories."""
    forbidden: set[Path] = set()
    for path in [*roots, *extra]:
        if path is None:
            continue
        current = _real(path)
        if current is None:
            continue
        while True:
            forbidden.add(current)
            parent = current.parent
            if parent == current:
                break
            current = parent
    return forbidden


def _strict_child(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
        except ValueError:
            continue
        if path != root:
            return True
    return False


def _skipped_dir_name(name: str) -> bool:
    """Hidden directories and system markers. Never walked, proposed, or deleted."""
    folded = _fold(name)
    if not folded or folded in {".", ".."}:
        return False
    if folded.startswith("."):
        return True
    return folded in _SYSTEM_DIRS


def _marker_file_name(name: str) -> bool:
    folded = _fold(name)
    if folded in _MARKER_FILES:
        return True
    return folded.startswith(".trash")


def _relative_is_protected(path: Path, roots: list[Path]) -> bool:
    """True when a component under a library root is hidden or a system marker.

    Components above the root are ignored, so a library stored under a hidden
    home directory is still cleaned.
    """
    for root in roots:
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        return any(_skipped_dir_name(part) for part in relative.parts)
    return False


def _collect_markers(roots: list[Path]) -> set[Path]:
    found: set[Path] = set()
    for root in roots:
        for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
            kept: list[str] = []
            for name in dirnames:
                if _skipped_dir_name(name):
                    resolved = _real(Path(current) / name)
                    if resolved is not None:
                        found.add(resolved)
                    continue
                kept.append(name)
            dirnames[:] = kept
            for name in filenames:
                if not _marker_file_name(name):
                    continue
                resolved = _real(Path(current) / name)
                if resolved is not None:
                    found.add(resolved)
    return found


def _holds_marker(path: Path, markers: set[Path]) -> bool:
    for marker in markers:
        if marker == path:
            return True
        try:
            marker.relative_to(path)
        except ValueError:
            continue
        return True
    return False


def _describe(path: Path) -> tuple[int, int, str]:
    """Recursive file count, byte size, and an extension tally."""
    counts: dict[str, int] = {}
    total = 0
    files = 0
    if path.is_dir():
        for current, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
            dirnames[:] = [name for name in dirnames if not _skipped_dir_name(name)]
            for name in filenames:
                if _marker_file_name(name):
                    continue
                file_path = Path(current) / name
                if file_path.is_symlink() or not file_path.is_file():
                    continue
                files += 1
                try:
                    total += file_path.stat().st_size
                except OSError:
                    pass
                suffix = file_path.suffix.lower() or "(sans extension)"
                counts[suffix] = counts.get(suffix, 0) + 1
    noun = "fichier" if files == 1 else "fichiers"
    detail = ", ".join(f"{suffix} ×{count}" for suffix, count in sorted(counts.items()))
    summary = f"{files} {noun}" + (f" · {detail}" if detail else "")
    if files:
        summary = f"Effectivement vide · {summary}"
    return files, total, summary


def _inside_library(path: Path, root: Path) -> bool:
    parts = [_fold(part) for part in path.relative_to(root).parts]
    return not any(_skipped_dir_name(part) for part in parts)


def _cleanable_dir(
    path: Path | None,
    root: Path,
    search: list[Path],
    forbidden: set[Path],
    markers: set[Path],
) -> bool:
    if path is None or path in forbidden or not _strict_child(path, search):
        return False
    if _relative_is_protected(path, search) or _holds_marker(path, markers):
        return False
    return _inside_library(path, root)


def _under_any(path: Path, parents: list[Path]) -> bool:
    for parent in parents:
        if path == parent:
            return True
        try:
            path.relative_to(parent)
        except ValueError:
            continue
        return True
    return False


def _has_video(path: Path) -> bool:
    if not path.is_dir():
        return False
    for child in path.rglob("*"):
        if child.is_file() and not child.is_symlink() and is_video(child):
            return True
    return False


def _effectively_empty(path: Path) -> bool:
    """No video, and every file is artwork, metadata, or junk."""
    return path.is_dir() and not _has_video(path) and _only_junk(path)


def _only_junk(path: Path) -> bool:
    if not path.is_dir():
        return False
    for child in path.rglob("*"):
        if child.is_symlink():
            return False
        if child.is_dir():
            continue
        if child.is_file() and not _junk_file(child):
            return False
    return True


def _junk_file(path: Path) -> bool:
    if any(_fold(part) == "@eadir" for part in path.parts):
        return True
    name = _fold(path.name)
    if name in _JUNK_FILES:
        return True
    return path.suffix.lower() in _ORPHAN_SUFFIXES


def _sidecar_stem(name: str) -> str | None:
    """Video stem for a leftover ``-thumb`` or episode ``.nfo``. Folder nfo files stay."""
    lower = name.casefold()
    for tail in _THUMB_TAILS:
        if lower.endswith(tail):
            return name[: -len(tail)]
    if lower.endswith(".nfo") and _FOLDER_NFO.match(name) is None:
        return name[:-4]
    return None


def _is_orphan_sidecar(path: Path) -> bool:
    if not path.is_file() or path.is_symlink() or _sidecar_stem(path.name) is None:
        return False
    return not _matching_video(path)


def _matching_video(path: Path) -> bool:
    base = _sidecar_stem(path.name)
    if base is None or not path.parent.is_dir():
        return False
    folded = base.casefold()
    try:
        children = [child for child in path.parent.iterdir() if child.is_file() and not child.is_symlink()]
    except OSError:
        return False
    names = {child.name.casefold(): child for child in children}
    direct = names.get(folded)
    if direct is not None and is_video(direct):
        return True
    for ext in VIDEO_EXT:
        candidate = names.get(f"{folded}{ext}")
        if candidate is not None and is_video(candidate):
            return True
    return False


def _ensure_trash(trash: Path | None, roots: list[Path]) -> Path:
    target = trash if trash is not None else (roots[0].parent / ".trash" if roots else None)
    if target is None:
        raise LibraryPlanError("La corbeille est introuvable.")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise LibraryPlanError("La corbeille est introuvable.") from exc
    resolved = _real(target)
    if resolved is None or not resolved.is_dir():
        raise LibraryPlanError("La corbeille est introuvable.")
    return resolved


def _move_one(src: Path, trash: Path, stamp: str) -> TrashedFile | None:
    """Rename one file into the trash. A failure leaves the file where it is."""
    if not src.is_file() or src.is_symlink():
        return None
    try:
        original = str(src.resolve())
        size = src.stat().st_size
        dest = trash / stamp / src.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{src.stem}-{src.stat().st_ino}{src.suffix}")
        atomic_move(src, dest)
    except OSError:
        return None
    return TrashedFile(original, str(dest.resolve()), size)


def _remove_empty_dirs(path: Path) -> None:
    """Remove directories that no longer contain anything. Files are left in place."""
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if not child.is_dir() or child.is_symlink():
            continue
        try:
            if any(child.iterdir()):
                return
            child.rmdir()
        except OSError:
            return
    try:
        if path.is_dir() and not any(path.iterdir()):
            path.rmdir()
    except OSError:
        return


def _fold(name: str) -> str:
    return unicodedata.normalize("NFC", name).casefold()


def _prune_empty(folder: Path, root: Path) -> None:
    current = folder
    while current != root and _is_inside(current, root):
        if not current.is_dir():
            break
        if any(current.iterdir()):
            break
        current.rmdir()
        current = current.parent

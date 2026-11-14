"""Compare each video's release name with the series folder that holds it.

The report is a dry run. Files move only when a person applies a line, and
every applied move is stored so it can be undone. A filename already rewritten
to the folder's name is visible only when that episode slot is also taken by
another file.
"""

import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.library_plan import LibraryPlanError, _folder_title, _rollback_paths, _tag
from mediatracker.core.naming import atomic_move, different_tmdb_ids, sanitize
from mediatracker.core.source_select import titles_match
from mediatracker.core.storage import is_under
from mediatracker.db.models import LibraryRename, Media, TrashEntry
from mediatracker.library.parse import is_video, normalize_title, parse_release, show_key
from mediatracker.library.regroup import _canonical_stem, _duplicate_warning, _file_size, _send_to_trash

_SIDE = frozenset({".srt", ".ass", ".ssa", ".sub", ".idx"})


@dataclass(frozen=True)
class AuditFile:
    path: str
    name: str
    folder: str
    file_title: str
    folder_title: str
    season: int | None
    episode: int | None
    kind: str
    reason: str
    target_folder: str
    target_name: str
    target_path: str
    blocked: str | None = None
    action: str = ""
    size: int = 0
    other_size: int | None = None
    warning: str | None = None


@dataclass(frozen=True)
class AuditReport:
    seen: int
    mismatch: int
    duplicate: int
    misnamed: int
    items: tuple[AuditFile, ...]


@dataclass(frozen=True)
class AuditApply:
    applied: bool
    rename_id: int | None
    moves: tuple[tuple[str, str], ...]
    skipped: tuple[str, ...]


@dataclass
class _Seen:
    path: Path
    root: Path
    folder: Path | None
    folder_title: str
    folder_year: int | None
    release_title: str
    release_year: int | None
    season: int | None
    episode: int | None


def audit_library(movies: Path, series: Path, *, skip: tuple[Path, ...] = ()) -> AuditReport:
    seen: list[_Seen] = []
    folders: dict[Path, list[Path]] = {}
    for root in (movies, series):
        resolved = root.resolve() if root.is_dir() else root
        folders[resolved] = _child_folders(root, skip)
        for path in _videos(root, skip):
            seen.append(_inspect(path, resolved))
    slot_groups: dict[tuple, list[_Seen]] = defaultdict(list)
    release_groups: dict[tuple, list[_Seen]] = defaultdict(list)
    for item in seen:
        if item.folder is not None and item.season is not None and item.episode is not None:
            slot_groups[(str(item.folder), item.season, item.episode)].append(item)
        titled = item.release_title and item.release_title != "Inconnu"
        if titled and item.season is not None and item.episode is not None:
            release_groups[(normalize_title(item.release_title), item.season, item.episode)].append(item)
    trash_rows, consumed = _trash_across_shows(seen)
    rows: list[AuditFile] = list(trash_rows)
    for item in seen:
        if item.path in consumed:
            continue
        slot_dups = slot_groups.get((str(item.folder), item.season, item.episode), []) if item.folder else []
        release_dups = release_groups.get((normalize_title(item.release_title), item.season, item.episode), [])
        rows.extend(_findings(item, folders.get(item.root, []), slot_dups, release_dups))
    rows.sort(key=lambda row: (row.kind, row.folder_title, row.season or 0, row.episode or 0, row.name))
    return AuditReport(
        seen=len(seen),
        mismatch=sum(1 for row in rows if row.kind == "mismatch"),
        duplicate=sum(1 for row in rows if row.kind == "duplicate"),
        misnamed=sum(1 for row in rows if row.kind == "misnamed"),
        items=tuple(rows),
    )


def duplicate_copies(
    folders: list[Path], *, skip: tuple[Path, ...] = ()
) -> tuple[tuple[AuditFile, ...], frozenset[Path]]:
    """Pair one resolved series by season and episode, whatever the folder.

    The canonical ``Title (Year) - SxxExx`` file is kept and omitted. Every
    other copy of that episode is a trash proposal. Nothing is moved.
    """
    seen: list[_Seen] = []
    for folder in folders:
        if not folder.is_dir():
            continue
        root = folder.parent
        for path in _videos(folder, skip):
            seen.append(_inspect(path, root))
    buckets: dict[tuple[int, int], list[_Seen]] = defaultdict(list)
    for item in seen:
        if item.season is None or item.episode is None:
            continue
        buckets[(item.season, item.episode)].append(item)
    rows, consumed = _trash_from_buckets(buckets)
    return tuple(rows), frozenset(consumed)


def apply_audit(
    db: Session,
    movies: Path,
    series: Path,
    paths: list[str],
    *,
    validated: bool,
    now: datetime | None = None,
    trash: Path | None = None,
    skip: tuple[Path, ...] = (),
) -> AuditApply:
    """Move or trash only the chosen files, and only after an explicit validation."""
    wanted = {_key(path) for path in paths}
    chosen = [row for row in audit_library(movies, series, skip=skip).items if _key(row.path) in wanted]
    if not validated:
        preview: list[tuple[str, str]] = []
        held: list[str] = []
        for row in chosen:
            if row.blocked:
                held.append(row.path)
            elif row.action == "trash":
                preview.append((row.path, row.path))
            elif row.target_path:
                preview.append((row.path, row.target_path))
            else:
                held.append(row.path)
        return AuditApply(False, None, tuple(preview), tuple(held))
    moment = now or datetime.now(timezone.utc)
    trash_dir = trash if trash is not None else series.parent / ".trash"
    applied: list[dict[str, str]] = []
    renamed: list[dict[str, str]] = []
    skipped: list[str] = []
    try:
        for row in chosen:
            src = Path(row.path)
            if row.action == "trash":
                if row.blocked or not src.is_file():
                    skipped.append(row.name)
                    continue
                _trash_release(db, src, trash_dir, moment, row.folder_title or row.name, applied)
                continue
            if row.blocked or not row.target_path:
                skipped.append(row.name)
                continue
            dest = Path(row.target_path)
            if different_tmdb_ids(src, dest):
                skipped.append(row.name)
                continue
            if not src.is_file():
                skipped.append(row.name)
                continue
            if src.resolve() == dest.resolve():
                continue
            if dest.exists():
                skipped.append(row.name)
                continue
            if not _destination_matches_release(dest, movies, series, row.file_title, _year_of(row)):
                skipped.append(row.name)
                continue
            before = len(applied)
            _move_with_sidecars(src, dest, applied)
            renamed.extend(applied[before:])
    except OSError as exc:
        _rollback_paths(applied)
        raise LibraryPlanError("Déplacement impossible.") from exc
    if not applied:
        return AuditApply(False, None, (), tuple(skipped))
    from mediatracker.core.cleanup import notify_cleanup_done

    trashed = list(
        db.scalars(
            select(TrashEntry).where(TrashEntry.deleted_at == moment, TrashEntry.reason == "duplicate")
        ).all()
    )
    if trashed:
        linked = db.get(Media, trashed[0].media_id) if trashed[0].media_id else None
        notify_cleanup_done(db, linked, "media", None, trashed, moment, reason="duplicate")
    logged = None
    if renamed:
        logged = LibraryRename(created_at=moment, moves=renamed)
        db.add(logged)
        db.flush()
    from mediatracker.core.cleanup_classify import request_classify

    request_classify(db)
    return AuditApply(
        True,
        logged.id if logged is not None else None,
        tuple((item["src"], item["dest"]) for item in applied),
        tuple(skipped),
    )


def _trash_across_shows(seen: list[_Seen]) -> tuple[list[AuditFile], set[Path]]:
    """Same show (folder title, non-conflicting TMDB) and the same SxxExx, any folder."""
    clusters = _show_clusters(seen)
    buckets: dict[tuple, list[_Seen]] = defaultdict(list)
    for item in seen:
        if item.folder is None or item.season is None or item.episode is None:
            continue
        buckets[(clusters.get(item.folder, id(item.folder)), item.season, item.episode)].append(item)
    return _trash_from_buckets(buckets)


def _trash_from_buckets(buckets: dict[tuple, list[_Seen]]) -> tuple[list[AuditFile], set[Path]]:
    rows: list[AuditFile] = []
    consumed: set[Path] = set()
    for group in buckets.values():
        unique: list[_Seen] = []
        known: set[Path] = set()
        for item in group:
            if item.path in known:
                continue
            known.add(item.path)
            unique.append(item)
        if len(unique) < 2:
            continue
        winner = max(unique, key=_keeper_key)
        if not _canonical_stem(winner.path):
            continue
        consumed.add(winner.path)
        for loser in unique:
            if loser.path == winner.path:
                continue
            consumed.add(loser.path)
            rows.append(_trash_finding(loser, winner))
    return rows, consumed


def _trash_finding(loser: _Seen, keeper: _Seen) -> AuditFile:
    warning = _duplicate_warning(keeper.path, loser.path)
    reason = (
        f"La copie déjà normée est conservée ({keeper.path.name}). "
        "Celle-ci porte un nom de release et part à la corbeille."
    )
    return AuditFile(
        path=str(loser.path),
        name=loser.path.name,
        folder=str(loser.folder) if loser.folder is not None else str(loser.root),
        file_title=loser.release_title,
        folder_title=loser.folder_title,
        season=loser.season,
        episode=loser.episode,
        kind="duplicate",
        reason=reason,
        target_folder="",
        target_name="",
        target_path="",
        blocked=None,
        action="trash",
        size=_file_size(loser.path),
        other_size=_file_size(keeper.path),
        warning=warning,
    )


def _keeper_key(item: _Seen) -> tuple[int, int, int, str]:
    canonical = 1 if _canonical_stem(item.path) else 0
    tagged = 1 if item.folder is not None and _tag(item.folder.name) else 0
    return (canonical, tagged, _file_size(item.path), item.path.name)


def _show_clusters(seen: list[_Seen]) -> dict[Path, int]:
    folders: list[Path] = []
    meta: dict[Path, tuple] = {}
    for item in seen:
        if item.folder is None or item.folder in meta:
            continue
        folders.append(item.folder)
        title, year = _folder_title(item.folder.name)
        meta[item.folder] = (item.root, show_key(title), year, _tag(item.folder.name))
    parent = list(range(len(folders)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for index, folder in enumerate(folders):
        root, key, year, tmdb = meta[folder]
        if not key:
            continue
        for other in range(index):
            oroot, okey, oyear, otmdb = meta[folders[other]]
            if root != oroot or key != okey:
                continue
            if tmdb and otmdb and tmdb != otmdb:
                continue
            if year and oyear and year != oyear:
                continue
            union(index, other)
    return {folder: find(index) for index, folder in enumerate(folders)}


def _trash_release(
    db: Session,
    src: Path,
    trash: Path,
    moment: datetime,
    title: str,
    applied: list[dict[str, str]],
) -> None:
    dest = _send_to_trash(db, src, trash, moment, None, title, reason="duplicate")
    applied.append({"src": str(src), "dest": str(dest)})
    for extra in _sidecars(src):
        if not extra.is_file():
            continue
        extra_dest = _send_to_trash(db, extra, trash, moment, None, title, reason="duplicate")
        applied.append({"src": str(extra), "dest": str(extra_dest)})


def _findings(
    item: _Seen,
    folders: list[Path],
    slot_dups: list[_Seen],
    release_dups: list[_Seen],
) -> list[AuditFile]:
    same = item.folder is not None and _same_show(
        item.release_title, item.release_year, item.folder_title, item.folder_year
    )
    target, blocked = _target_folder(item, folders, same)
    year = item.release_year or (item.folder_year if same else None)
    target_path = None
    if target is not None:
        target_path = _jellyfin_path(
            target, item.release_title, year, item.season, item.episode, item.path.suffix
        )
    if target_path is not None:
        blocked = different_tmdb_ids(item.path, target_path) or blocked
    if target_path is not None and target_path.exists() and target_path.resolve() != item.path.resolve():
        blocked = blocked or f"Un fichier occupe déjà {target_path.name}."
    kind = _kind(item, same, slot_dups, release_dups, target_path)
    if kind is None:
        return []
    reason = _reason(item, kind, same, slot_dups, release_dups)
    return [
        AuditFile(
            path=str(item.path),
            name=item.path.name,
            folder=str(item.folder) if item.folder is not None else str(item.root),
            file_title=item.release_title,
            folder_title=item.folder_title,
            season=item.season,
            episode=item.episode,
            kind=kind,
            reason=reason,
            target_folder=str(target) if target is not None else "",
            target_name=target_path.name if target_path is not None else "",
            target_path=str(target_path) if target_path is not None and not blocked else "",
            blocked=blocked,
        )
    ]


def _kind(
    item: _Seen,
    same: bool,
    slot_dups: list[_Seen],
    release_dups: list[_Seen],
    target_path: Path | None,
) -> str | None:
    if not same:
        return "mismatch"
    duplicated = len(slot_dups) > 1 or len({row.path for row in release_dups}) > 1
    if duplicated:
        return "duplicate"
    if target_path is not None and target_path.resolve() != item.path.resolve():
        return "misnamed"
    return None


def _reason(
    item: _Seen,
    kind: str,
    same: bool,
    slot_dups: list[_Seen],
    release_dups: list[_Seen],
) -> str:
    if kind == "mismatch":
        place = item.folder_title or "la racine"
        text = f"« {item.release_title} » est dans le dossier « {place} »."
    elif kind == "misnamed":
        text = "Le fichier est dans le bon dossier, mais pas au nom Jellyfin."
    else:
        text = "Deux fichiers portent le même épisode."
    foreign = [
        row
        for row in slot_dups
        if row.path != item.path
        and not _same_show(row.release_title, row.release_year, item.folder_title, item.folder_year)
    ]
    if same and foreign:
        names = ", ".join(sorted({row.release_title for row in foreign}))
        text += f" Celui-ci porte le nom du dossier et partage l'épisode avec : {names}."
    elif len({row.path for row in release_dups}) > 1 and kind != "mismatch":
        text += " Le même épisode existe aussi ailleurs."
    return text


def _target_folder(item: _Seen, folders: list[Path], same: bool) -> tuple[Path | None, str | None]:
    if same and item.folder is not None:
        return item.folder, None
    matches = [
        folder
        for folder in folders
        if _same_show(item.release_title, item.release_year, *_folder_title(folder.name))
    ]
    if len(matches) == 1:
        return matches[0], None
    exact = [
        folder
        for folder in matches
        if item.release_year is not None and _folder_title(folder.name)[1] == item.release_year
    ]
    if len(exact) == 1:
        return exact[0], None
    tagged = [folder for folder in matches if _tag(folder.name)]
    tagged_year = _folder_title(tagged[0].name)[1] if len(tagged) == 1 else None
    year_ok = item.release_year is None or tagged_year in {None, item.release_year}
    if len(tagged) == 1 and year_ok:
        return tagged[0], None
    if len(matches) > 1:
        names = ", ".join(folder.name for folder in matches)
        return None, f"Plusieurs dossiers correspondent ({names}). Rien n'est déplacé."
    return None, (
        "Aucun dossier de cette série. Le nom de release n'est pas un dossier : "
        "utilisez le regroupement vers Title (Year) [tmdbid-N]."
    )


def _destination_matches_release(dest: Path, movies: Path, series: Path, title: str, year: int | None) -> bool:
    for root in (movies, series):
        try:
            relative = dest.resolve().relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        if not relative.parts:
            return False
        folder_title, folder_year = _folder_title(relative.parts[0])
        return _same_show(title, year, folder_title, folder_year)
    return False


def _move_with_sidecars(src: Path, dest: Path, applied: list[dict[str, str]]) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_move(src, dest)
    applied.append({"src": str(src), "dest": str(dest)})
    for extra in _sidecars(src):
        suffix = extra.name[len(src.stem) :]
        extra_dest = dest.parent / f"{dest.stem}{suffix}"
        if extra_dest.exists():
            continue
        atomic_move(extra, extra_dest)
        applied.append({"src": str(extra), "dest": str(extra_dest)})


def _sidecars(video: Path) -> list[Path]:
    if not video.parent.is_dir():
        return []
    found: list[Path] = []
    for path in video.parent.iterdir():
        if path.is_file() and path.suffix.lower() in _SIDE and path.name.startswith(video.stem):
            found.append(path)
    return found


def _inspect(path: Path, root: Path) -> _Seen:
    release = parse_release(path.name)
    folder = _series_folder(path, root)
    if folder is None:
        folder_title, folder_year = "", None
    else:
        folder_title, folder_year = _folder_title(folder.name)
    return _Seen(
        path=path.resolve(),
        root=root,
        folder=folder,
        folder_title=folder_title,
        folder_year=folder_year,
        release_title=release.title,
        release_year=release.year,
        season=release.season,
        episode=release.episode,
    )


def _series_folder(path: Path, root: Path) -> Path | None:
    try:
        relative = path.resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    if len(relative.parts) < 2:
        return None
    return (root / relative.parts[0]).resolve()


def _same_show(file_title: str, file_year: int | None, folder_title: str, folder_year: int | None) -> bool:
    if not file_title or not folder_title or not titles_match(file_title, folder_title):
        return False
    if file_year is not None and folder_year is not None and file_year != folder_year:
        return False
    return True


def _jellyfin_path(
    folder: Path,
    title: str,
    year: int | None,
    season: int | None,
    episode: int | None,
    ext: str,
) -> Path:
    stem = sanitize(title)
    if year:
        stem = f"{stem} ({year})"
    suffix = ext.lower()
    if season is not None and episode is not None:
        return folder / f"Season {season:02d}" / f"{stem} - S{season:02d}E{episode:02d}{suffix}"
    return folder / f"{stem}{suffix}"


def _videos(root: Path, skip: tuple[Path, ...] = ()):
    if not root.is_dir() or is_under(root, skip):
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if not name.startswith(".") and not is_under(Path(dirpath) / name, skip)
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            if is_under(path, skip):
                continue
            if is_video(path):
                yield path


def _child_folders(root: Path, skip: tuple[Path, ...] = ()) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(
        (
            child.resolve()
            for child in root.iterdir()
            if child.is_dir() and not child.name.startswith(".") and not is_under(child, skip)
        ),
        key=lambda item: item.name,
    )


def _year_of(row: AuditFile) -> int | None:
    folder_title, year = _folder_title(Path(row.target_folder).name) if row.target_folder else ("", None)
    del folder_title
    return year


def _key(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except OSError:
        return path

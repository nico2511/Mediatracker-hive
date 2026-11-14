"""Dry-run regroup of release-named library folders onto the tracked fiche.

A folder such as ``Détective Démo FRENCH … Wawacity town`` is not a series. This plans
moves into ``Title (Year) [tmdbid-N]/Season NN/`` with the canonical file name.
An existing ``Title (Year)`` folder is merged into the tagged one. A duplicate
episode keeps the name ``Title (Year) - SxxExx``; the other goes to the trash.
Nothing is written until ``validated`` is set. The journal keeps the release name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.cleanup import format_bytes
from mediatracker.core.library_plan import LibraryPlanError, _folder_title, _prune_empty, _rollback_paths, _tag
from mediatracker.core.naming import atomic_move, different_tmdb_ids, filing_folder, library_destination
from mediatracker.core.quality_profile import RESOLUTION_RANK, normalize_resolution
from mediatracker.core.source_select import release_core, titles_match
from mediatracker.core.storage import is_under
from mediatracker.db.models import LibraryFile, LibraryRename, Media, TrashEntry
from mediatracker.library.parse import is_video, parse_release, season_number, show_key
from mediatracker.library.release_name import retarget_path

_EXTRAS = frozenset({"poster.jpg", "fanart.jpg", "landscape.jpg", "banner.jpg", "tvshow.nfo", "movie.nfo"})
_SIDECAR_TAILS = (
    ".nfo",
    ".srt",
    ".ass",
    ".ssa",
    ".sub",
    ".idx",
    "-thumb.jpg",
    "-thumb.png",
    "-fanart.jpg",
    "-poster.jpg",
    "-banner.jpg",
    "-landscape.jpg",
    "-clearlogo.png",
    "-logo.png",
)


@dataclass(frozen=True)
class RegroupMove:
    src: str
    dest: str
    name: str
    original_name: str
    blocked: str | None = None
    action: str = "move"
    size: int = 0
    other_size: int | None = None
    warning: str | None = None


@dataclass(frozen=True)
class RegroupGroup:
    media_id: int | None
    title: str
    year: int | None
    tmdb_id: int | None
    media_type: str
    canonical: str
    match: str
    folders: tuple[str, ...]
    moves: tuple[RegroupMove, ...]
    empty_after: tuple[str, ...]
    loose: bool = False


@dataclass(frozen=True)
class RegroupReport:
    groups: tuple[RegroupGroup, ...]
    unmatched: tuple[str, ...]
    ambiguous: tuple[str, ...]
    empty: tuple[str, ...]
    loose: tuple[RegroupGroup, ...] = ()


@dataclass
class _Bucket:
    media: Media
    root: Path
    match: str = ""
    folders: list[Path] = field(default_factory=list)


def plan_regroup(
    db: Session,
    movies: Path,
    series: Path,
    *,
    protected: tuple[Path, ...] = (),
    skip: tuple[Path, ...] = (),
) -> RegroupReport:
    medias = list(db.scalars(select(Media)).all())
    originals = {
        row.path: row.original_name
        for row in db.scalars(select(LibraryFile)).all()
        if row.original_name
    }
    buckets: dict[int, _Bucket] = {}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    for root, kind in ((movies, "film"), (series, "series")):
        if not root.is_dir():
            continue
        for folder in sorted(root.iterdir(), key=lambda item: item.name):
            if not folder.is_dir() or folder.name.startswith(".") or _protected(folder, protected):
                continue
            if is_under(folder, skip):
                continue
            has_video = _has_video(folder, skip)
            if not has_video and not _only_sidecars(folder):
                continue
            media, how = _match(folder, medias, kind)
            if media is None and has_video and how != "ambiguous":
                media, how = _match_videos(folder, medias, kind)
            if not has_video and media is None:
                continue
            if how == "ambiguous":
                ambiguous.append(str(folder.resolve()))
                continue
            if media is None:
                unmatched.append(str(folder.resolve()))
                continue
            bucket = buckets.get(media.id)
            if bucket is None:
                bucket = _Bucket(media, root.resolve())
                buckets[media.id] = bucket
            bucket.folders.append(folder.resolve())
            if how == "tmdb" or not bucket.match:
                bucket.match = how
    groups: list[RegroupGroup] = []
    for bucket in sorted(buckets.values(), key=lambda item: item.media.title.casefold()):
        group = _group(bucket, originals)
        if group.folders:
            groups.append(group)
    from mediatracker.core.library_plan import preview_empty_folders

    empty = tuple(row.path for row in preview_empty_folders(movies, series, protected=protected))
    absorbed = {str(folder.resolve()) for bucket in buckets.values() for folder in bucket.folders}
    loose = _loose_duplicates(movies, series, absorbed, protected, skip)
    loose_paths = {folder for group in loose for folder in group.folders}
    unmatched = [path for path in unmatched if path not in loose_paths]
    return RegroupReport(
        groups=tuple(groups),
        unmatched=tuple(unmatched),
        ambiguous=tuple(ambiguous),
        empty=empty,
        loose=tuple(loose),
    )


def apply_regroup(
    db: Session,
    movies: Path,
    series: Path,
    media_ids: list[int],
    *,
    validated: bool,
    protected: tuple[Path, ...] = (),
    now: datetime | None = None,
    trash: Path | None = None,
    include_loose: bool = False,
    skip: tuple[Path, ...] = (),
) -> tuple[bool, int | None, RegroupReport]:
    """Move only the chosen fiches, and only after an explicit validation."""
    report = plan_regroup(db, movies, series, protected=protected, skip=skip)
    wanted = set(media_ids)
    chosen = [group for group in report.groups if group.media_id in wanted]
    if not validated:
        return False, None, report
    moment = now or datetime.now(timezone.utc)
    trash_dir = trash if trash is not None else series.parent / ".trash"
    applied: list[dict[str, str]] = []
    journal: list[dict[str, str]] = []
    try:
        for group in chosen:
            pending = [move for move in group.moves if not move.blocked]
            for move in pending:
                if move.action != "trash":
                    continue
                dest = _send_to_trash(db, Path(move.src), trash_dir, moment, group.media_id, group.title)
                applied.append({"src": move.src, "dest": str(dest), "original_name": move.original_name})
            for move in pending:
                if move.action == "trash":
                    continue
                src = Path(move.src)
                dest = Path(move.dest)
                if different_tmdb_ids(src, dest):
                    continue
                if not src.is_file() or src.resolve() == dest.resolve():
                    continue
                if dest.exists():
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                atomic_move(src, dest)
                retarget_path(db, src, dest, original=move.original_name)
                recorded = {"src": str(src), "dest": str(dest), "original_name": move.original_name}
                applied.append(recorded)
                journal.append(recorded)
            for folder in group.empty_after:
                _prune_tree(Path(folder), _root_for(group, movies, series))
        if include_loose:
            for group in report.loose:
                for move in group.moves:
                    if move.blocked or move.action != "trash":
                        continue
                    dest = _send_to_trash(db, Path(move.src), trash_dir, moment, group.media_id, group.title)
                    applied.append({"src": move.src, "dest": str(dest), "original_name": move.original_name})
    except OSError as exc:
        _rollback_paths(applied)
        raise LibraryPlanError("Déplacement impossible.") from exc
    if not applied:
        return False, None, report
    logged = None
    if journal:
        logged = LibraryRename(created_at=moment, moves=journal)
        db.add(logged)
        db.flush()
    from mediatracker.core.cleanup_classify import request_classify

    request_classify(db)
    fresh = plan_regroup(db, movies, series, protected=protected, skip=skip)
    return True, logged.id if logged is not None else None, fresh


def _group(bucket: _Bucket, originals: dict[str, str]) -> RegroupGroup:
    media = bucket.media
    canonical = filing_folder(bucket.root, title=media.title, year=media.year, tmdb_id=media.tmdb_id).resolve()
    sources = [folder for folder in bucket.folders if folder.resolve() != canonical]
    moves = _plan_moves(media, canonical, sources, originals)
    planned = {Path(move.src).resolve() for move in moves if not move.blocked}
    empty_after: list[str] = []
    for folder in sources:
        files = [path.resolve() for path in folder.rglob("*") if path.is_file()]
        if files and all(path in planned for path in files):
            empty_after.append(str(folder))
    return RegroupGroup(
        media_id=media.id,
        title=media.title,
        year=media.year,
        tmdb_id=media.tmdb_id,
        media_type=media.type,
        canonical=str(canonical),
        match=bucket.match or "titre",
        folders=tuple(str(folder) for folder in sources),
        moves=tuple(moves),
        empty_after=tuple(empty_after),
    )


@dataclass
class _Candidate:
    path: Path
    dest: Path
    season: int | None
    episode: int | None
    original: str
    blocked: str | None
    slot: tuple


def _plan_moves(media: Media, canonical: Path, sources: list[Path], originals: dict[str, str]) -> list[RegroupMove]:
    """Canonical names, merged untagged folder, and one keeper per episode."""
    videos = _videos(media, canonical, sources, originals)
    by_slot: dict[tuple, list[_Candidate]] = {}
    for video in videos:
        by_slot.setdefault(video.slot, []).append(video)
    moves: list[RegroupMove] = []
    claimed: set[Path] = set()
    for slot, group in by_slot.items():
        if slot[0] == "blocked":
            for item in group:
                moves.append(_move(item, "move"))
                claimed.add(item.path.resolve())
            continue
        ranked = sorted(group, key=lambda item: (_normalized(item), _score(item.path, item.dest)), reverse=True)
        winner = ranked[0]
        here = {item.path.resolve() for item in group}
        occupied = winner.dest.exists() and winner.dest.resolve() not in here
        if winner.blocked:
            moves.append(_move(winner, "move"))
            claimed.add(winner.path.resolve())
        elif winner.path.resolve() != winner.dest.resolve():
            if occupied:
                blocked = _Candidate(
                    path=winner.path,
                    dest=winner.dest,
                    season=winner.season,
                    episode=winner.episode,
                    original=winner.original,
                    blocked=f"Un fichier occupe déjà {winner.dest.name}.",
                    slot=winner.slot,
                )
                moves.append(_move(blocked, "move"))
            else:
                moves.append(_move(winner, "move"))
            claimed.add(winner.path.resolve())
        else:
            claimed.add(winner.path.resolve())
        if winner.blocked or occupied:
            continue
        for extra in _episode_sidecars(winner.path):
            tail = extra.name[len(winner.path.stem) :]
            dest = winner.dest.parent / f"{winner.dest.stem}{tail}"
            if extra.resolve() == dest.resolve():
                claimed.add(extra.resolve())
                continue
            if dest.exists() and dest.resolve() != extra.resolve():
                moves.append(_loose(dest, "trash", originals))
                claimed.add(dest.resolve())
            moves.append(
                RegroupMove(
                    src=str(extra.resolve()),
                    dest=str(dest),
                    name=dest.name,
                    original_name=originals.get(str(extra.resolve())) or extra.name,
                    action="move",
                )
            )
            claimed.add(extra.resolve())
        for loser in ranked[1:]:
            if loser.path.resolve() == winner.path.resolve():
                continue
            moves.append(_move(loser, "trash", keeper=winner.path if _normalized(winner) else None))
            claimed.add(loser.path.resolve())
            for extra in _episode_sidecars(loser.path):
                moves.append(_loose(extra, "trash", originals))
                claimed.add(extra.resolve())
    for folder in sources:
        for extra in sorted(folder.iterdir(), key=lambda item: item.name):
            if not extra.is_file() or extra.name.lower() not in _EXTRAS or extra.resolve() in claimed:
                continue
            dest = canonical / extra.name
            if dest.exists() and dest.resolve() != extra.resolve():
                moves.append(_loose(extra, "trash", originals))
            else:
                moves.append(_loose(extra, "move", originals, dest))
            claimed.add(extra.resolve())
        for path in folder.rglob("*"):
            if not path.is_file() or path.resolve() in claimed or is_video(path):
                continue
            if _sidecarish(path):
                moves.append(_loose(path, "trash", originals))
                claimed.add(path.resolve())
    return moves


def _videos(media: Media, canonical: Path, sources: list[Path], originals: dict[str, str]) -> list[_Candidate]:
    found: list[_Candidate] = []
    seen: set[Path] = set()
    roots = list(sources)
    if canonical.is_dir():
        roots.append(canonical)
    device = _device(canonical if canonical.exists() else canonical.parent)
    for folder in roots:
        home = folder if folder.resolve() != canonical else canonical
        for video in sorted(path for path in folder.rglob("*") if path.is_file() and is_video(path)):
            resolved = video.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            found.append(_candidate(media, canonical, home, video, device, originals))
    return found


def _candidate(
    media: Media,
    canonical: Path,
    folder: Path,
    video: Path,
    device: int | None,
    originals: dict[str, str],
) -> _Candidate:
    release = parse_release(video.name)
    season = _season(video, folder) if media.type == "series" else None
    episode = release.episode
    original = originals.get(str(video.resolve())) or originals.get(str(video)) or video.name
    blocked = None
    if media.type == "series" and season is None:
        blocked = "Saison illisible. Le fichier n'est pas déplacé."
        dest = canonical / video.name
        slot: tuple = ("blocked", str(video.resolve()))
    elif device is not None and _device(video) not in {None, device}:
        blocked = "Le déplacement doit rester sur le même volume."
        dest = canonical / video.name
        slot = ("blocked", str(video.resolve()))
    else:
        dest = _destination(media, canonical, video, season, episode)
        if media.type == "series" and episode is None:
            slot = ("solo", str(video.resolve()))
        elif media.type == "series":
            slot = ("episode", season, episode)
        else:
            slot = ("film",)
    clash = different_tmdb_ids(video, dest)
    if clash:
        blocked = clash
        slot = ("blocked", str(video.resolve()))
    return _Candidate(video.resolve(), dest, season, episode, original, blocked, slot)


def _destination(media: Media, canonical: Path, video: Path, season: int | None, episode: int | None) -> Path:
    if media.type == "series" and season is not None and episode is not None:
        return library_destination(
            movies_dir=canonical.parent,
            series_dir=canonical.parent,
            media_type="series",
            title=media.title,
            year=media.year,
            season=season,
            episode=episode,
            ext=video.suffix or ".mkv",
            tmdb_id=media.tmdb_id,
            library_folder=canonical,
        )
    if media.type != "series":
        return library_destination(
            movies_dir=canonical.parent,
            series_dir=canonical.parent,
            media_type="film",
            title=media.title,
            year=media.year,
            season=None,
            episode=None,
            ext=video.suffix or ".mkv",
            tmdb_id=media.tmdb_id,
            library_folder=canonical,
        )
    folder = canonical / f"Season {season:02d}" if season is not None else canonical
    return folder / video.name


def _score(path: Path, dest: Path) -> tuple[int, int, int, int]:
    release = parse_release(path.name)
    quality = normalize_resolution(release.quality)
    rank = RESOLUTION_RANK.get(quality or "", 0)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    try:
        placed = path.resolve() == dest.resolve()
    except OSError:
        placed = False
    named = path.name == dest.name
    return (rank, size, int(placed), int(named))


def _move(item: _Candidate, action: str, keeper: Path | None = None) -> RegroupMove:
    dest = item.dest if action == "move" else item.path
    other = _file_size(keeper) if keeper is not None else None
    warning = _duplicate_warning(keeper, item.path) if keeper is not None else None
    return RegroupMove(
        src=str(item.path),
        dest=str(dest),
        name=item.dest.name if action == "move" else item.path.name,
        original_name=item.original,
        blocked=item.blocked,
        action="move" if item.blocked else action,
        size=_file_size(item.path),
        other_size=other,
        warning=warning,
    )


def _loose(path: Path, action: str, originals: dict[str, str], dest: Path | None = None) -> RegroupMove:
    target = dest if dest is not None else path
    return RegroupMove(
        src=str(path.resolve()),
        dest=str(target),
        name=target.name if action == "move" else path.name,
        original_name=originals.get(str(path.resolve())) or path.name,
        action=action,
        size=_file_size(path),
    )


def _episode_sidecars(video: Path) -> list[Path]:
    if not video.parent.is_dir():
        return []
    found: list[Path] = []
    for path in video.parent.iterdir():
        if not path.is_file() or path.resolve() == video.resolve() or not path.name.startswith(video.stem):
            continue
        tail = path.name[len(video.stem) :].lower()
        if tail in _SIDECAR_TAILS or tail.endswith((".srt", ".ass", ".ssa", ".sub", ".idx", ".nfo")):
            found.append(path)
    return found


def _sidecarish(path: Path) -> bool:
    name = path.name.lower()
    if name in _EXTRAS:
        return True
    return any(name.endswith(tail) for tail in _SIDECAR_TAILS)


def _only_sidecars(folder: Path) -> bool:
    files = [path for path in folder.rglob("*") if path.is_file()]
    return bool(files) and all(_sidecarish(path) for path in files)


def _send_to_trash(
    db: Session,
    src: Path,
    trash: Path,
    now: datetime,
    media_id: int | None,
    title: str,
    reason: str = "regroup",
) -> Path:
    if not src.is_file():
        raise OSError("FILE_MISSING")
    original = str(src.resolve())
    size = src.stat().st_size
    dest = trash / now.strftime("%Y%m%dT%H%M%S") / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}-{src.stat().st_ino}{dest.suffix}")
    atomic_move(src, dest)
    db.add(
        TrashEntry(
            original_path=original,
            trash_path=str(dest.resolve()),
            size=size,
            media_id=media_id,
            label=(title or src.name)[:300],
            reason=(reason or "regroup")[:16],
            deleted_at=now,
            purge_after=now + timedelta(days=7),
        )
    )
    row = db.scalar(select(LibraryFile).where(LibraryFile.path == original))
    if row is not None:
        row.status = "missing"
        row.missing_at = now
    return dest


_CANONICAL_EPISODE = re.compile(r".+ \(\d{4}\) - S\d{2}E\d{2}$")
_SMALL_COPY = 50 * 1024 * 1024
_MUCH_LARGER = 300 * 1024 * 1024


def _normalized(item: _Candidate) -> int:
    if item.blocked:
        return 0
    if item.path.name == item.dest.name or _canonical_stem(item.path):
        return 1
    return 0


def _canonical_stem(path: Path) -> bool:
    return _CANONICAL_EPISODE.match(path.stem) is not None


def _file_size(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _duplicate_warning(keeper: Path, other: Path) -> str | None:
    kept = _file_size(keeper)
    extra = _file_size(other)
    notes: list[str] = []
    if kept < _SMALL_COPY:
        notes.append(f"La copie normée est très petite ({format_bytes(kept)}).")
    if kept > 0 and extra >= kept * 2 and extra - kept >= _MUCH_LARGER:
        notes.append(f"L'autre copie est nettement plus grosse ({format_bytes(extra)} contre {format_bytes(kept)}).")
    return " ".join(notes) or None


def _match_videos(folder: Path, medias: list[Media], kind: str) -> tuple[Media | None, str]:
    """A release folder with one episode still belongs to the fiche of that title."""
    hits: dict[int, Media] = {}
    try:
        videos = [path for path in folder.rglob("*") if path.is_file() and is_video(path)]
    except OSError:
        return None, "none"
    for video in videos:
        release = parse_release(video.name)
        key = show_key(release.title)
        if not key:
            continue
        for row in medias:
            if row.type != kind:
                continue
            names = [name for name in (row.title, row.original_title) if name]
            if not any(show_key(name) == key for name in names):
                continue
            if release.year is not None and row.year is not None and release.year != row.year:
                continue
            hits[row.id] = row
    if len(hits) == 1:
        return next(iter(hits.values())), "fichier"
    return None, "none"


def _loose_duplicates(
    movies: Path,
    series: Path,
    absorbed: set[str],
    protected: tuple[Path, ...],
    skip: tuple[Path, ...] = (),
) -> list[RegroupGroup]:
    """Same episode in folders that have no fiche. The normalized name is kept."""
    buckets: dict[tuple, list[Path]] = {}
    homes: dict[tuple, set[str]] = {}
    titles: dict[tuple, str] = {}
    for root, kind in ((movies, "film"), (series, "series")):
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            if not folder.is_dir() or folder.name.startswith(".") or _protected(folder, protected):
                continue
            if is_under(folder, skip):
                continue
            resolved = str(folder.resolve())
            if resolved in absorbed:
                continue
            try:
                videos = [
                    path
                    for path in folder.rglob("*")
                    if path.is_file() and is_video(path) and not is_under(path, skip)
                ]
            except OSError:
                continue
            for video in videos:
                release = parse_release(video.name)
                if kind == "series" and (release.season is None or release.episode is None):
                    continue
                key = show_key(release.title) or show_key(folder.name)
                if not key:
                    continue
                slot = (kind, key, release.season, release.episode)
                buckets.setdefault(slot, []).append(video.resolve())
                homes.setdefault(slot, set()).add(resolved)
                titles.setdefault(slot, release.title or folder.name)
    groups: list[RegroupGroup] = []
    for slot, paths in buckets.items():
        unique: list[Path] = []
        seen: set[Path] = set()
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            unique.append(path)
        if len(unique) < 2:
            continue
        normalized = [path for path in unique if _canonical_stem(path)]
        if not normalized:
            continue
        keeper = max(normalized, key=_file_size)
        moves = [
            RegroupMove(
                src=str(path),
                dest=str(path),
                name=path.name,
                original_name=path.name,
                action="trash",
                size=_file_size(path),
                other_size=_file_size(keeper),
                warning=_duplicate_warning(keeper, path),
            )
            for path in unique
            if path != keeper and not _canonical_stem(path)
        ]
        if not moves:
            continue
        kind, _key, _season, _episode = slot
        groups.append(
            RegroupGroup(
                media_id=None,
                title=titles[slot],
                year=None,
                tmdb_id=None,
                media_type="series" if kind == "series" else "film",
                canonical=str(keeper.parent),
                match="fichier",
                folders=tuple(sorted(homes[slot])),
                moves=tuple(moves),
                empty_after=(),
                loose=True,
            )
        )
    return groups


def _match(folder: Path, medias: list[Media], kind: str) -> tuple[Media | None, str]:
    title, year = _folder_title(folder.name)
    tag = _tag(folder.name)
    same_type = [row for row in medias if row.type == kind]
    if tag is not None:
        tagged = [row for row in same_type if row.tmdb_id == tag]
        if len(tagged) == 1:
            return tagged[0], "tmdb"
        if len(tagged) > 1:
            return None, "ambiguous"
        return None, "none"
    hits = [row for row in same_type if _title_hit(title, year, row)]
    years = {row.year for row in hits if row.year is not None}
    if year is None and len(years) > 1:
        return None, "ambiguous"
    if len(hits) == 1:
        return hits[0], "titre"
    if len(hits) > 1:
        return None, "ambiguous"
    return None, "none"


def _title_hit(title: str, year: int | None, media: Media) -> bool:
    names = [name for name in (media.title, media.original_title) if name]
    if not title or not any(_strict(title, name) for name in names):
        return False
    if year is not None and media.year is not None and year != media.year:
        return False
    return True


def _strict(left: str, right: str) -> bool:
    if titles_match(left, right):
        return True
    return _flat(left) == _flat(right) and _flat(left) != ""


def _flat(value: str) -> str:
    text = (value or "").replace("'", "").replace("’", "").replace("`", "")
    return release_core(text)


def _season(video: Path, folder: Path) -> int | None:
    release = parse_release(video.name)
    if release.season is not None:
        return release.season
    try:
        relative = video.parent.resolve().relative_to(folder.resolve())
    except (ValueError, OSError):
        return None
    for part in relative.parts:
        number = season_number(part)
        if number is not None:
            return number
    return None


def _has_video(folder: Path, skip: tuple[Path, ...] = ()) -> bool:
    return any(path.is_file() and is_video(path) and not is_under(path, skip) for path in folder.rglob("*"))


def _protected(folder: Path, protected: tuple[Path, ...]) -> bool:
    try:
        resolved = folder.resolve()
    except OSError:
        return True
    for root in protected:
        try:
            if resolved == root.resolve():
                return True
        except OSError:
            continue
    return False


def _device(path: Path) -> int | None:
    current = path
    while True:
        try:
            return current.stat().st_dev
        except FileNotFoundError:
            parent = current.parent
            if parent == current:
                return None
            current = parent
        except OSError:
            return None


def _prune_tree(folder: Path, root: Path) -> None:
    if not folder.is_dir():
        return
    for child in list(folder.iterdir()):
        if child.is_dir():
            _prune_tree(child, root)
    _prune_empty(folder, root)


def _root_for(group: RegroupGroup, movies: Path, series: Path) -> Path:
    return series.resolve() if group.media_type == "series" else movies.resolve()

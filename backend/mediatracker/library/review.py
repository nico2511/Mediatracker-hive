"""One library pass: match each folder to a series, then check the files inside.

Nothing on disk moves here. Merge plans and file moves are proposals until
``validated`` is set.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.core.library_plan import LibraryPlanError, _rollback_paths, list_folders, preview_empty_folders
from mediatracker.core.naming import atomic_move, different_tmdb_ids
from mediatracker.core.source_select import titles_match
from mediatracker.db.models import JellyfinLibraryItem, LibraryFile, LibraryRename, Media
from mediatracker.library.audit import AuditFile, _sidecars, audit_library, duplicate_copies
from mediatracker.library.parse import is_video, normalize_title, parse_release


@dataclass(frozen=True)
class SeasonSummary:
    season: int
    episodes: int
    label: str


@dataclass(frozen=True)
class ReviewFolder:
    path: str
    name: str
    title: str
    year: int | None
    media_type: str
    tmdb_id: int | None
    file_count: int
    canonical: bool
    seasons: tuple[SeasonSummary, ...]


@dataclass(frozen=True)
class ReviewMove:
    src: str
    dest: str
    name: str
    blocked: str | None = None


@dataclass(frozen=True)
class ReviewFile:
    path: str
    name: str
    kind: str
    reason: str
    season: int | None
    episode: int | None
    target_path: str
    target_name: str
    blocked: str | None
    original_name: str | None = None
    action: str = ""
    size: int = 0
    other_size: int | None = None
    warning: str | None = None


@dataclass(frozen=True)
class ReviewSeries:
    key: str
    title: str
    year: int | None
    tmdb_id: int | None
    poster_url: str | None
    media_type: str
    media_id: int | None
    followed: bool
    problem: str | None
    duplicate: bool
    merge_hint: str | None
    folders: tuple[ReviewFolder, ...]
    merges: tuple[ReviewMove, ...]
    files: tuple[ReviewFile, ...]
    empty_after: tuple[str, ...] = ()


@dataclass(frozen=True)
class SuspectFile:
    path: str
    name: str
    size: int
    media_id: int | None
    message: str


@dataclass(frozen=True)
class ReviewReport:
    series: tuple[ReviewSeries, ...]
    empty: tuple
    mismatch: int
    duplicate_folders: int
    unmatched: int
    suspect_files: tuple[SuspectFile, ...] = ()


def build_review(
    db: Session,
    movies: Path,
    series: Path,
    *,
    protected: tuple[Path, ...] = (),
    skip: tuple[Path, ...] = (),
) -> ReviewReport:
    listed = list_folders(movies, series, skip=skip)
    medias = list(db.scalars(select(Media)).all())
    groups = _group_folders(listed, medias, _jellyfin_by_folder(db, listed))
    problems = audit_library(movies, series, skip=skip)
    by_folder: dict[str, list[AuditFile]] = defaultdict(list)
    for item in problems.items:
        if item.kind in {"mismatch", "duplicate"}:
            by_folder[item.folder].append(item)
    posters = _posters(groups, medias)
    originals = {
        row.path: row.original_name
        for row in db.scalars(select(LibraryFile)).all()
        if row.original_name
    }
    built: list[ReviewSeries] = []
    for key, members in groups.items():
        built.append(_series(key, members, medias, posters, by_folder, originals, skip))
    built.sort(key=lambda row: (_rank(row), row.title.casefold()))
    empty = tuple(preview_empty_folders(movies, series, protected=protected))
    return ReviewReport(
        series=tuple(built),
        empty=empty,
        mismatch=sum(1 for row in built for item in row.files if item.kind == "mismatch"),
        duplicate_folders=sum(1 for row in built if row.duplicate),
        unmatched=sum(1 for row in built if row.problem == "unmatched"),
        suspect_files=_suspect_files(db),
    )


def _suspect_files(db: Session) -> tuple[SuspectFile, ...]:
    rows = list(
        db.scalars(
            select(LibraryFile).where(LibraryFile.integrity_suspect.is_(True)).order_by(LibraryFile.path)
        ).all()
    )
    built: list[SuspectFile] = []
    for row in rows:
        path = Path(row.path)
        built.append(
            SuspectFile(
                path=row.path,
                name=path.name,
                size=row.size,
                media_id=row.media_id,
                message=row.note or "Fichier suspect",
            )
        )
    return tuple(built)


def apply_merge(
    db: Session,
    movies: Path,
    series: Path,
    key: str,
    *,
    validated: bool,
    protected: tuple[Path, ...] = (),
    skip: tuple[Path, ...] = (),
):
    """Move files from duplicate folders into the canonical one. Dry-run unless validated."""
    report = build_review(db, movies, series, protected=protected, skip=skip)
    group = next((row for row in report.series if row.key == key and row.duplicate), None)
    if group is None:
        raise LibraryPlanError("Ces dossiers ne sont pas des doublons.")
    pending = [move for move in group.merges if not move.blocked]
    if not validated:
        return False, None, pending
    applied: list[dict[str, str]] = []
    try:
        for move in pending:
            src = Path(move.src)
            dest = Path(move.dest)
            if different_tmdb_ids(src, dest) or not src.is_file() or dest.exists():
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            from mediatracker.library.release_name import journal_original, retarget_path

            original = journal_original(db, src) or src.name
            atomic_move(src, dest)
            retarget_path(db, src, dest, original=original)
            applied.append({"src": str(src), "dest": str(dest), "original_name": original})
            for extra in _sidecars(src):
                suffix = extra.name[len(src.stem) :]
                extra_dest = dest.parent / f"{dest.stem}{suffix}"
                if extra_dest.exists():
                    continue
                atomic_move(extra, extra_dest)
                applied.append({"src": str(extra), "dest": str(extra_dest)})
    except OSError as exc:
        _rollback_paths(applied)
        raise LibraryPlanError("Déplacement impossible.") from exc
    if not applied:
        return False, None, pending
    logged = LibraryRename(created_at=datetime.now(timezone.utc), moves=applied)
    db.add(logged)
    db.flush()
    from mediatracker.core.cleanup_classify import request_classify

    request_classify(db)
    return True, logged.id, pending


def _series(key, members, medias, posters, by_folder, originals, skip: tuple[Path, ...] = ()) -> ReviewSeries:
    tmdb_id = next((row.tmdb_id for row in members if row.tmdb_id), None)
    media = _media(medias, tmdb_id, members)
    title = media.title if media is not None else members[0].title
    year = media.year if media is not None and media.year else members[0].year
    canonical = _canonical(members, tmdb_id, title, year)
    folders = tuple(_review_folder(row, canonical) for row in members)
    trash_rows, consumed = duplicate_copies([Path(row.path) for row in members], skip=skip)
    merges = _merges(canonical, members, consumed) if len(members) > 1 else ()
    files_list = [
        _review_file(item, originals.get(item.path))
        for row in members
        for item in by_folder.get(str(Path(row.path).resolve()), [])
        if Path(item.path).resolve() not in consumed
    ]
    files_list.extend(_review_file(item, originals.get(item.path)) for item in trash_rows)
    files_list.sort(key=lambda item: (item.season or 0, item.episode or 0, item.name))
    files = tuple(files_list)
    leaving = {Path(item.path).resolve() for item in trash_rows}
    empty_after = _empty_after(members, canonical, leaving)
    duplicate = len(members) > 1
    unmatched = tmdb_id is None and media is None and not _known_title(members)
    has_duplicate = any(item.kind == "duplicate" for item in files)
    problem = "mismatch" if any(item.kind == "mismatch" for item in files) else None
    if problem is None and (duplicate or has_duplicate):
        problem = "duplicate"
    if problem is None and unmatched:
        problem = "unmatched"
    hint = None
    if merges:
        hint = (
            f"Dossiers en double. Proposition : déplacer les fichiers vers « {canonical.name} ». "
            "Aperçu seulement, rien n'est déplacé."
        )
    elif has_duplicate:
        hint = (
            "La copie déjà normée est conservée. La copie au nom de release part à la corbeille, "
            "elle n'est pas effacée. Aperçu seulement, rien n'est déplacé."
        )
    poster = posters.get(key)
    if poster is None and media is not None:
        poster = media.poster_url
    return ReviewSeries(
        key=key,
        title=title or members[0].name,
        year=year,
        tmdb_id=tmdb_id or (media.tmdb_id if media is not None else None),
        poster_url=poster,
        media_type=members[0].media_type,
        media_id=media.id if media is not None else None,
        followed=bool(media is not None and media.followed),
        problem=problem,
        duplicate=duplicate,
        merge_hint=hint,
        folders=folders,
        merges=merges,
        files=files,
        empty_after=empty_after,
    )


def _review_folder(row, canonical) -> ReviewFolder:
    return ReviewFolder(
        path=row.path,
        name=row.name,
        title=row.title,
        year=row.year,
        media_type=row.media_type,
        tmdb_id=row.tmdb_id,
        file_count=len(row.files),
        canonical=Path(row.path).resolve() == Path(canonical.path).resolve(),
        seasons=_seasons(row.files),
    )


def _review_file(item: AuditFile, original_name: str | None = None) -> ReviewFile:
    return ReviewFile(
        path=item.path,
        name=item.name,
        kind=item.kind,
        reason=item.reason,
        season=item.season,
        episode=item.episode,
        target_path=item.target_path,
        target_name=item.target_name,
        blocked=item.blocked,
        original_name=original_name,
        action=item.action,
        size=item.size,
        other_size=item.other_size,
        warning=item.warning,
    )


def _group_folders(listed, medias, jellyfin: dict[str, tuple[int | None, str | None]] | None = None) -> dict[str, list]:
    parent = list(range(len(listed)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    links = jellyfin or {}
    resolved = [_resolved_identity(row, medias, links) for row in listed]
    for index, row in enumerate(listed):
        row_tmdb = resolved[index]
        row_jellyfin = _jellyfin_id(row, links)
        for other in range(index):
            other_tmdb = resolved[other]
            other_jellyfin = _jellyfin_id(listed[other], links)
            same_type = listed[other].media_type == row.media_type
            if row_jellyfin and other_jellyfin and row_jellyfin == other_jellyfin and same_type:
                union(index, other)
                continue
            if row_tmdb and other_tmdb and row_tmdb == other_tmdb and same_type:
                union(index, other)
                continue
            if row_tmdb and other_tmdb and row_tmdb != other_tmdb:
                continue
            if same_type and _titles_compatible(row, listed[other]):
                union(index, other)
    groups: dict[str, list] = defaultdict(list)
    buckets: dict[int, list] = defaultdict(list)
    for index, row in enumerate(listed):
        buckets[find(index)].append((row, resolved[index]))
    for members in buckets.values():
        tmdb_id = next((tmdb for _row, tmdb in members if tmdb), None)
        rows = [replace(row, tmdb_id=tmdb_id or row.tmdb_id) for row, _tmdb in members]
        if tmdb_id:
            key = f"tmdb:{rows[0].media_type}:{tmdb_id}"
        elif len(rows) == 1:
            key = f"folder:{rows[0].path}"
        else:
            year = rows[0].year or "na"
            key = f"title:{rows[0].media_type}:{normalize_title(rows[0].title)}:{year}"
        groups[key].extend(rows)
    return groups


def _resolved_identity(row, medias, jellyfin: dict[str, tuple[int | None, str | None]]) -> int | None:
    found = _resolved_tmdb(row, medias)
    if found:
        return found
    tmdb, series_id = jellyfin.get(str(Path(row.path).resolve()), (None, None))
    if tmdb:
        return tmdb
    if not series_id:
        return None
    media = next(
        (
            item
            for item in medias
            if item.type == row.media_type and (item.jellyfin_series_id or "") == series_id and item.tmdb_id
        ),
        None,
    )
    return media.tmdb_id if media is not None else None


def _jellyfin_id(row, jellyfin: dict[str, tuple[int | None, str | None]]) -> str | None:
    _tmdb, series_id = jellyfin.get(str(Path(row.path).resolve()), (None, None))
    return series_id or None


def _jellyfin_by_folder(db: Session, listed) -> dict[str, tuple[int | None, str | None]]:
    """Folder path to (TMDB, Jellyfin series id) when the cache names that folder."""
    from mediatracker.library.jellyfin_align import _norm

    rows = list(db.scalars(select(JellyfinLibraryItem)).all())
    if not rows:
        return {}
    series_tmdb: dict[str, int | None] = {}
    for row in rows:
        if row.item_type not in {"Series", "Movie"}:
            continue
        series_id = (row.series_id or row.jellyfin_id or "").strip()
        if series_id:
            series_tmdb[series_id] = row.tmdb_id
    folders = [(str(Path(row.path).resolve()), Path(row.path).resolve()) for row in listed]
    found: dict[str, tuple[int | None, str | None]] = {}
    for row in rows:
        series_id = (row.series_id or (row.jellyfin_id if row.item_type in {"Series", "Movie"} else "") or "").strip()
        if not series_id:
            continue
        tmdb = row.tmdb_id if row.tmdb_id is not None else series_tmdb.get(series_id)
        for candidate in (row.local_path, row.path):
            if not candidate:
                continue
            try:
                path = Path(candidate).resolve()
            except OSError:
                path = Path(candidate)
            for key, folder in folders:
                inside = path == folder
                if not inside:
                    try:
                        path.relative_to(folder)
                        inside = True
                    except ValueError:
                        inside = _norm(str(path)) == _norm(key)
                if not inside:
                    continue
                current = found.get(key)
                if current is None or (current[0] is None and tmdb is not None):
                    found[key] = (tmdb, series_id)
    return found


def _resolved_tmdb(row, medias) -> int | None:
    if row.tmdb_id:
        return row.tmdb_id
    hits = [
        media
        for media in medias
        if media.type == row.media_type
        and media.tmdb_id
        and titles_match(row.title, media.title)
        and (row.year is None or media.year is None or row.year == media.year)
    ]
    exact = [media for media in hits if row.year is not None and media.year == row.year]
    if len(exact) == 1:
        return exact[0].tmdb_id
    if row.year is None and len(hits) == 1:
        return hits[0].tmdb_id
    return None


def _titles_compatible(left, right) -> bool:
    if not left.title or not right.title or not titles_match(left.title, right.title):
        return False
    if left.year is not None and right.year is not None and left.year != right.year:
        return False
    return True


def _known_title(members) -> bool:
    return len(members) > 1


def _media(medias, tmdb_id, members) -> Media | None:
    kind = members[0].media_type
    if tmdb_id:
        found = next((row for row in medias if row.tmdb_id == tmdb_id and row.type == kind), None)
        if found is not None:
            return found
    hits = [
        row
        for row in medias
        if row.type == kind and any(titles_match(row.title, member.title) for member in members)
    ]
    exact = [
        row
        for row in hits
        if any(member.year is not None and row.year == member.year for member in members)
    ]
    if len(exact) == 1:
        return exact[0]
    if len(hits) == 1:
        return hits[0]
    return None


def _canonical(members, tmdb_id, title, year):
    def score(row) -> tuple[int, int]:
        points = len(row.files)
        name = row.name
        if tmdb_id and f"tmdbid-{tmdb_id}" in name.casefold():
            points += 1000
        if "wawacity" in name.casefold():
            points -= 500
        expected = f"{title} ({year})" if year else title
        if name.casefold().startswith(expected.casefold()):
            points += 100
        return points, len(row.files)

    return max(members, key=score)


def _empty_after(members, canonical, leaving: set[Path]) -> tuple[str, ...]:
    found: list[str] = []
    root = Path(canonical.path).resolve()
    for row in members:
        folder = Path(row.path).resolve()
        if folder == root or not folder.is_dir():
            continue
        videos = [path.resolve() for path in folder.rglob("*") if path.is_file() and is_video(path)]
        if videos and all(path in leaving for path in videos):
            found.append(str(folder))
    return tuple(found)


def _merges(canonical, members, consumed: set[Path] | frozenset[Path]) -> tuple[ReviewMove, ...]:
    moves: list[ReviewMove] = []
    root = Path(canonical.path).resolve()
    for row in members:
        if Path(row.path).resolve() == root:
            continue
        for video in Path(row.path).rglob("*"):
            if not video.is_file() or not is_video(video):
                continue
            if video.resolve() in consumed:
                continue
            release = parse_release(video.name)
            if release.season is not None:
                dest = root / f"Season {release.season:02d}" / video.name
            else:
                dest = root / video.name
            blocked = different_tmdb_ids(video, dest)
            if blocked is None and dest.exists():
                blocked = f"Un fichier occupe déjà {dest.name}."
            moves.append(ReviewMove(str(video.resolve()), str(dest), video.name, blocked))
    return tuple(moves)


def _seasons(files) -> tuple[SeasonSummary, ...]:
    counts: dict[int, set] = defaultdict(set)
    for item in files:
        if item.season is None:
            continue
        counts[item.season].add(item.episode if item.episode is not None else item.path)
    return tuple(
        SeasonSummary(season, len(episodes), f"S{season:02d} · {len(episodes)} ép.")
        for season, episodes in sorted(counts.items())
    )


def _posters(groups, medias) -> dict[str, str | None]:
    found: dict[str, str | None] = {}
    for key, members in groups.items():
        tmdb_id = next((row.tmdb_id for row in members if row.tmdb_id), None)
        media = _media(medias, tmdb_id, members)
        poster = media.poster_url if media is not None and media.poster_url else None
        if poster is None:
            for row in members:
                local = _local_poster(Path(row.path))
                if local is not None:
                    poster = f"/api/v1/library/poster?path={quote(row.path)}"
                    break
        found[key] = poster
    return found


def _local_poster(folder: Path) -> Path | None:
    for name in ("poster.jpg", "folder.jpg", "cover.jpg"):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


def _rank(row: ReviewSeries) -> int:
    if row.problem == "mismatch":
        return 0
    if row.problem == "duplicate":
        return 1
    if row.problem == "unmatched":
        return 2
    return 3


def local_poster_file(folder: Path, movies: Path, series: Path) -> Path | None:
    resolved = folder.resolve()
    if not any(_inside(resolved, root) for root in (movies, series) if root.is_dir()):
        return None
    return _local_poster(resolved)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return False
    return path != root.resolve()

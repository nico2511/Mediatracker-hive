"""Videos already stored under the Jellyfin name, before a download starts or at filing."""

from pathlib import Path

from mediatracker.core.naming import canonical_folder_name, filing_folder, sanitize
from mediatracker.core.quality_profile import RESOLUTION_RANK, normalize_resolution
from mediatracker.core.storage import is_under
from mediatracker.library.parse import is_video, parse_release, show_key


def valid_video(path: Path) -> bool:
    """A real video file. Empty files, samples, and symlinks stay out."""
    try:
        if not path.is_file() or path.is_symlink():
            return False
        return is_video(path) and path.stat().st_size > 0
    except OSError:
        return False


def find_library_video(
    *,
    movies_dir: Path,
    series_dir: Path,
    media_type: str,
    title: str,
    year: int | None,
    season: int | None,
    episode: int | None,
    tmdb_id: int | None,
) -> Path | None:
    """Canonical episode or film already on disk, including folder and case variants."""
    stems = _stems(
        media_type=media_type, title=title, year=year, season=season, episode=episode, tmdb_id=tmdb_id
    )
    if not stems:
        return None
    found: list[Path] = []
    for folder in _folders(
        movies_dir=movies_dir,
        series_dir=series_dir,
        media_type=media_type,
        title=title,
        year=year,
        season=season,
        tmdb_id=tmdb_id,
    ):
        if not folder.is_dir():
            continue
        try:
            children = list(folder.iterdir())
        except OSError:
            continue
        for path in children:
            if valid_video(path) and path.stem.casefold() in stems:
                found.append(path)
    best: Path | None = None
    for path in found:
        if best is None or better_video(path, best):
            best = path
    return best


def find_episode_on_disk(
    *,
    movies_dir: Path,
    series_dir: Path,
    media_type: str,
    title: str,
    year: int | None,
    season: int | None,
    episode: int | None,
    tmdb_id: int | None,
    skip: tuple[Path, ...] = (),
) -> Path | None:
    """Any video of this episode, including a release name such as ``Show.S01E08.FiNAL``."""
    canonical = find_library_video(
        movies_dir=movies_dir,
        series_dir=series_dir,
        media_type=media_type,
        title=title,
        year=year,
        season=season,
        episode=episode,
        tmdb_id=tmdb_id,
    )
    if canonical is not None and not is_under(canonical, skip):
        return canonical
    if media_type == "series" and (season is None or episode is None):
        return None
    root = series_dir if media_type == "series" else movies_dir
    if not root.is_dir():
        return None
    wanted = show_key(title)
    if not wanted:
        return None
    best: Path | None = None
    try:
        folders = [
            path
            for path in root.iterdir()
            if path.is_dir() and not path.name.startswith(".") and not is_under(path, skip)
        ]
    except OSError:
        return None
    for folder in folders:
        for path in _videos_under(folder, skip):
            parsed = parse_release(path.name)
            if media_type == "series" and (parsed.season != season or parsed.episode != episode):
                continue
            if media_type != "series" and (parsed.season is not None or parsed.episode is not None):
                continue
            if not _same_show(folder.name, parsed.title, wanted, year, parsed.year, tmdb_id):
                continue
            if best is None or better_video(path, best):
                best = path
    return best


def _videos_under(folder: Path, skip: tuple[Path, ...] = ()) -> list[Path]:
    found: list[Path] = []
    if is_under(folder, skip):
        return found
    try:
        paths = folder.rglob("*")
    except OSError:
        return found
    for path in paths:
        if is_under(path, skip):
            continue
        if valid_video(path):
            found.append(path)
    return found


def _same_show(
    folder_name: str,
    file_title: str,
    wanted: str,
    year: int | None,
    file_year: int | None,
    tmdb_id: int | None,
) -> bool:
    from mediatracker.core.library_plan import _tag

    if tmdb_id is not None and _tag(folder_name) == tmdb_id:
        return True
    if year is not None and file_year is not None and year != file_year:
        return False
    keys = {show_key(folder_name), show_key(file_title)}
    return wanted in keys


def quality_rank(path: Path, *, names: tuple[str, ...] = (), qualities: tuple[str | None, ...] = ()) -> int | None:
    """Best known resolution rank. Unknown names stay unknown so size can decide."""
    ranks: list[int] = []
    for quality in qualities:
        token = normalize_resolution(quality)
        if token:
            ranks.append(RESOLUTION_RANK[token])
    labels = (path.name, *names)
    for name in labels:
        if not name:
            continue
        token = normalize_resolution(parse_release(name).quality)
        if token:
            ranks.append(RESOLUTION_RANK[token])
    if not ranks:
        return None
    return max(ranks)


def better_video(
    challenger: Path,
    incumbent: Path,
    *,
    challenger_names: tuple[str, ...] = (),
    challenger_qualities: tuple[str | None, ...] = (),
    incumbent_names: tuple[str, ...] = (),
    incumbent_qualities: tuple[str | None, ...] = (),
) -> bool:
    """True when ``challenger`` should replace the file already in the library."""
    incoming = quality_rank(challenger, names=challenger_names, qualities=challenger_qualities)
    current = quality_rank(incumbent, names=incumbent_names, qualities=incumbent_qualities)
    if incoming is not None and current is not None and incoming != current:
        return incoming > current
    return _size(challenger) > _size(incumbent)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _stems(
    *,
    media_type: str,
    title: str,
    year: int | None,
    season: int | None,
    episode: int | None,
    tmdb_id: int | None,
) -> set[str]:
    stem = sanitize(title)
    if year:
        stem = f"{stem} ({year})"
    names = [stem]
    if tmdb_id:
        names.append(f"{stem} [tmdbid-{tmdb_id}]")
    if media_type == "series":
        if season is None or episode is None:
            return set()
        coded: list[str] = []
        for base in names:
            coded.append(f"{base} - S{season:02d}E{episode:02d}")
            if season < 10 or episode < 10:
                coded.append(f"{base} - S{season}E{episode}")
        names = coded
    return {name.casefold() for name in names}


def canonical_folders_for_media(
    *,
    movies_dir: Path,
    series_dir: Path,
    media_type: str,
    title: str,
    year: int | None,
    tmdb_id: int | None,
    seasons: list[int] | None = None,
) -> list[Path]:
    """Jellyfin-style folders to scan for one fiche, optionally limited to some seasons."""
    if media_type == "film":
        return _folders(
            movies_dir=movies_dir,
            series_dir=series_dir,
            media_type=media_type,
            title=title,
            year=year,
            season=None,
            tmdb_id=tmdb_id,
        )
    numbers = seasons if seasons is not None else []
    folders: list[Path] = []
    seen: set[str] = set()
    for season in numbers:
        for folder in _folders(
            movies_dir=movies_dir,
            series_dir=series_dir,
            media_type=media_type,
            title=title,
            year=year,
            season=season,
            tmdb_id=tmdb_id,
        ):
            key = str(folder)
            if key in seen:
                continue
            seen.add(key)
            folders.append(folder)
    return folders


def _folders(
    *,
    movies_dir: Path,
    series_dir: Path,
    media_type: str,
    title: str,
    year: int | None,
    season: int | None,
    tmdb_id: int | None,
) -> list[Path]:
    root = series_dir if media_type == "series" else movies_dir
    bases = [
        filing_folder(root, title=title, year=year, tmdb_id=tmdb_id),
        root / canonical_folder_name(title, year, tmdb_id),
        root / canonical_folder_name(title, year, None),
    ]
    folders: list[Path] = []
    seen: set[str] = set()
    for base in bases:
        candidates = [base]
        if media_type == "series" and season is not None:
            candidates = [base / f"Season {season:02d}"]
            if season < 10:
                candidates.append(base / f"Season {season}")
        for folder in candidates:
            key = str(folder)
            if key in seen:
                continue
            seen.add(key)
            folders.append(folder)
    return folders

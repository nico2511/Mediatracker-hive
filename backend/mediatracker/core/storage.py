"""Resolved library, download, and trash directories.

Environment values are the default. A non-empty setting in the database wins,
so Réglages and the wizard can override them. Empty settings fall back to the
environment, then to the historical layout under ``DATA_DIR``.
"""

import logging
import os
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StorageLayout:
    media_dir: Path
    movies_dir: Path
    series_dir: Path
    downloads_dir: Path
    trash_dir: Path


def storage_layout(db: Session, settings: Settings) -> StorageLayout:
    data = Path(settings.data_dir)
    media = on_disk(_chosen(db, "media_dir", settings.media_dir) or (data / "media"))
    movies = on_disk(
        _chosen(db, "library_movies_path", settings.movies_dir or settings.library_movies_path)
        or (media / "Films")
    )
    series = on_disk(
        _chosen(db, "library_series_path", settings.series_dir or settings.library_series_path)
        or (media / "Series")
    )
    downloads = on_disk(_chosen(db, "downloads_dir", settings.downloads_dir) or (data / "downloads"))
    trash = on_disk(_chosen(db, "trash_dir", settings.trash_dir) or (data / ".trash"))
    return StorageLayout(
        media_dir=media,
        movies_dir=movies,
        series_dir=series,
        downloads_dir=downloads,
        trash_dir=trash,
    )


def incomplete_dir(layout: StorageLayout) -> Path:
    return layout.downloads_dir / "incomplete"


def library_skip(layout: StorageLayout) -> tuple[Path, ...]:
    """Download and trash directories. They are not film or series folders.

    On the NAS the trash is ``/data/.mediatracker/trash``, next to ``Séries``.
    A library root that also contains that directory, or a trash folder placed
    inside the series tree, must not be scanned: the moved files would come
    back as episodes or duplicates. Jellyfin uses the same exclusion.
    """
    return (layout.downloads_dir, incomplete_dir(layout), layout.trash_dir)


def is_under(path: Path, roots: tuple[Path, ...]) -> bool:
    """True when ``path`` is one of ``roots`` or a file inside it."""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    for root in roots:
        try:
            base = root.resolve()
        except OSError:
            base = root
        if resolved == base:
            return True
        try:
            resolved.relative_to(base)
        except ValueError:
            continue
        return True
    return False


def device_id(path: Path) -> int | None:
    """Device of ``path``, or of the nearest existing parent when it is missing."""
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


def same_filesystem_warning(
    layout: StorageLayout,
    device_of: Callable[[Path], int | None] = device_id,
) -> str:
    """Empty when films, series, downloads and trash share one ``st_dev``."""
    labeled = (
        ("films", layout.movies_dir),
        ("séries", layout.series_dir),
        ("téléchargements", layout.downloads_dir),
        ("corbeille", layout.trash_dir),
    )
    missing = [name for name, path in labeled if device_of(path) is None]
    if missing:
        return "Impossible de vérifier le disque de " + ", ".join(missing) + "."
    if len({device_of(path) for _, path in labeled}) == 1:
        return ""
    details = ", ".join(f"{name} ({path})" for name, path in labeled)
    return (
        "Les dossiers films, séries, téléchargements et corbeille ne sont pas sur le même "
        f"système de fichiers ({details}). Le rangement doit rester un renommage : "
        "montez-les sur le même disque."
    )


def on_disk(path: Path) -> Path:
    """Use the directory that exists when only the Unicode form differs.

    Linux treats NFC and NFD as two names. A compose value written one way
    still reaches a library folder created the other way, and does not
    create a second ``Séries``.
    """
    path = Path(path)
    if path.exists():
        return path
    if not path.is_absolute():
        return path
    current = Path(path.anchor)
    parts = path.parts[1:]
    for index, part in enumerate(parts):
        direct = current / part
        if direct.exists():
            current = direct
            continue
        found = _normalized_child(current, part)
        if found is not None:
            current = found
            continue
        return current.joinpath(*parts[index:])
    return current


def _normalized_child(directory: Path, name: str) -> Path | None:
    if not directory.is_dir():
        return None
    target = unicodedata.normalize("NFC", name)
    try:
        children = list(directory.iterdir())
    except OSError:
        return None
    for child in children:
        if unicodedata.normalize("NFC", child.name) == target:
            return child
    return None


def ensure_runtime_dirs(
    db: Session,
    settings: Settings,
    *,
    chown: Callable[..., None] = os.chown,
    geteuid: Callable[[], int] = os.geteuid,
) -> None:
    """Create the download and trash directories when they are missing.

    As root (the s6 init), they are handed to ``PUID:PGID``. The API process
    already runs as that user and only creates what is still absent.
    """
    layout = storage_layout(db, settings)
    targets = [layout.downloads_dir, incomplete_dir(layout), layout.trash_dir]
    for path in targets:
        path.mkdir(parents=True, exist_ok=True)
    if geteuid() != 0:
        return
    parents = {layout.downloads_dir.parent, layout.trash_dir.parent}
    for path in [*parents, *targets]:
        if path == Path(path.anchor) or not path.exists():
            continue
        try:
            chown(path, settings.puid, settings.pgid)
        except OSError:
            logger.warning("Droits %s:%s non appliqués sur %s.", settings.puid, settings.pgid, path)


def log_filesystem_layout(db: Session, settings: Settings) -> None:
    warning = same_filesystem_warning(storage_layout(db, settings))
    if warning:
        logger.warning("%s", warning)


def library_on_local_disk(db: Session, settings: Settings) -> bool:
    """True when films and series share one local volume with downloads and trash.

    Fast canonical probes apply only in that layout. Split or remote mounts keep
    the full library search used by the scanner.
    """
    layout = storage_layout(db, settings)
    return not same_filesystem_warning(layout)


def _chosen(db: Session, key: str, env_value: str | Path | None) -> Path | None:
    row = db.get(Setting, key)
    if row is not None and row.value:
        return Path(row.value)
    if env_value is None or str(env_value) == "":
        return None
    return Path(env_value)

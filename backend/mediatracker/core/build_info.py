"""Version stamped into the image at Docker build time.

``APP_VERSION``, ``GIT_SHA`` and ``BUILD_DATE`` are build args. The running
process reads them from the environment. A dev checkout without those args
keeps the package version and omits the commit and the date.

When ``GIT_SHA`` is not a real commit (e.g. ``dev``), a per-image ``.build-id``
file written at ``docker build`` supplies a stable fingerprint suffix.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path

from mediatracker import __version__

_SHA = re.compile(r"^[0-9a-f]{7,40}$")
_BUILD_ID_FILE = ".build-id"


def app_version() -> str:
    raw = (os.environ.get("MEDIATRACKER_VERSION") or __version__).strip()
    return raw or __version__


def short_sha() -> str | None:
    raw = (os.environ.get("MEDIATRACKER_GIT_SHA") or os.environ.get("GIT_SHA") or "").strip().lower()
    if not _SHA.fullmatch(raw):
        return None
    return raw[:7]


def build_date() -> str | None:
    raw = (os.environ.get("MEDIATRACKER_BUILD_DATE") or "").strip()
    return raw or None


@lru_cache(maxsize=1)
def build_id() -> str | None:
    for path in _build_id_paths():
        try:
            raw = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw:
            return raw
    return None


def short_build_id() -> str | None:
    raw = build_id()
    if not raw:
        return None
    compact = raw.replace("-", "").lower()
    if not compact:
        return None
    return compact[:8]


def _build_id_paths() -> tuple[Path, ...]:
    core_dir = Path(__file__).resolve().parent
    return (core_dir / _BUILD_ID_FILE, Path("/app") / _BUILD_ID_FILE)


def fingerprint_suffix() -> str:
    """Git short SHA when present, otherwise a short docker build id."""
    return short_sha() or short_build_id() or ""

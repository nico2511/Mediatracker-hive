"""Nettoyage du texte cause (même politique que events/scrub, sans URL ni secret)."""

from __future__ import annotations

import re

from mediatracker.providers.downloaders.aria2 import scrub_url

_MAX_LEN = 500
_ABS_PATH = re.compile(r"(?<![\w])/(?:[\w.-]+/)+[\w.-]+")


def scrub_cause(text: str | None) -> str | None:
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        return None
    cleaned = scrub_url(stripped)
    cleaned = _ABS_PATH.sub("[chemin]", cleaned)
    if len(cleaned) > _MAX_LEN:
        return cleaned[: _MAX_LEN - 3] + "..."
    return cleaned


def scrub_exception_cause(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return f"{type(exc).__name__}:{code}"
    return type(exc).__name__
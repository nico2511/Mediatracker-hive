"""Split a release heading into a title and the version tokens glued on the end.

Wawacity headings look like « Archipel Pilote - Saison 2 - VF ». Once the
season is lifted out, a naive cut leaves « Archipel Pilote - - VF ». This
keeps that knowledge out of the dashboard: any caller gets a clean title and
the trailing language or quality, in order.
"""

import re

from mediatracker.core.display_title import clean_display_title

# Longer aliases first so « truefrench » is not read as a shorter token.
_TOKEN = (
    ("truefrench", "VFF"),
    ("vostfr", "VOSTFR"),
    ("vfq", "VFQ"),
    ("vff", "VFF"),
    ("multi", "MULTI"),
    ("webrrip", "HD"),
    ("webrip", "HD"),
    ("web-dl", "HD"),
    ("webdl", "HD"),
    ("hdlight", "HDLight"),
    ("2160p", "4K"),
    ("1080p", "1080p"),
    ("720p", "720p"),
    ("480p", "SD"),
    ("uhd", "4K"),
    ("4k", "4K"),
    ("hd", "HD"),
    ("sd", "SD"),
    ("vf", "VF"),
    ("vo", "VO"),
)
_TAIL = re.compile(
    r"(?i)(?:\s*[-–—|]\s*|\s+)(" + "|".join(alias for alias, _name in _TOKEN) + r")\s*$"
)
_CANON = {alias: name for alias, name in _TOKEN}


def split_release_title(raw: str) -> tuple[str, list[str]]:
    """Return the title and the trailing version labels, left to right.

    A token is only removed from the end, and only when a dash or a space
    separates it from the title. « HD Forever » stays whole.
    """
    text = re.sub(r"\s+", " ", (raw or "").strip())
    found: list[str] = []
    while text:
        match = _TAIL.search(text)
        if not match:
            break
        found.append(_CANON[match.group(1).lower()])
        text = text[: match.start()].strip(" -–—:|")
        text = re.sub(r"\s+", " ", text).strip(" -–—:|")
    found.reverse()
    return text, found


def release_caption(
    raw: str,
    *,
    quality: str | None = None,
    language: str | None = None,
    season: int | None = None,
) -> tuple[str, str | None]:
    """Clean title, plus a single version line such as « Saison 2 · HD · VF »."""
    title, tokens = split_release_title(raw)
    parts: list[str] = []
    if season:
        parts.append(f"Saison {season}")
    for bit in (quality, language, *tokens):
        if bit and bit not in parts:
            parts.append(bit)
    shown = clean_display_title(title) or clean_display_title(raw)
    return shown or (raw or "").strip(), (" · ".join(parts) or None)

"""Titles shown to a person, without empty parentheses or release-language junk."""

import re

# Parentheses that only repeat a language, a resolution, or nothing at all.
_JUNK = re.compile(
    r"(?i)[\(\[]\s*(?:"
    r"french|truefrench|vostfr|vff|vfq|multi|vf|vo|en|"
    r"2160p|1080p|720p|480p|4k|uhd|hdlight|hd|sd|"
    r"webrip|web-dl|webdl|bluray|bdrip"
    r")\s*[\)\]]"
)
_EMPTY = re.compile(r"[\(\[]\s*[\)\]]")
_SPACE = re.compile(r"\s+")


def clean_display_title(raw: str | None) -> str:
    """Drop ``()`` and ``(FRENCH)``-style tags. The stored title is left unchanged."""
    text = _SPACE.sub(" ", (raw or "").strip())
    if not text:
        return ""
    cleaned = text
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _JUNK.sub(" ", cleaned)
        cleaned = _EMPTY.sub(" ", cleaned)
        cleaned = _SPACE.sub(" ", cleaned).strip(" -–—:|")
    return cleaned or text

"""Verdicts par source pendant un passage du job de téléchargement."""

from __future__ import annotations

from dataclasses import dataclass

from mediatracker.core.pipeline.source_attempt_journal import format_source_attempt_message
from mediatracker.core.source_state import _source_label

_PASS_ATTR = "_download_pass_verdicts"
_BLOCKING_CODES = frozenset({"AUTH_BLOCKED", "DISK_FULL"})
_SEPARATOR = " · "


@dataclass(frozen=True)
class _PassVerdict:
    code: str
    message: str
    issue: str


def begin_download_pass(row: object) -> None:
    setattr(row, _PASS_ATTR, {})


def reset_pass_error_fields(row: object) -> None:
    code = getattr(row, "error_code", None)
    if code in _BLOCKING_CODES:
        return
    row.error_code = None  # type: ignore[attr-defined]
    row.error = None  # type: ignore[attr-defined]


def record_download_pass_verdict(
    row: object,
    source: str,
    *,
    code: str,
    message: str,
    issue: str,
) -> None:
    bag = getattr(row, _PASS_ATTR, None)
    if bag is None:
        bag = {}
        setattr(row, _PASS_ATTR, bag)
    short = (message or "").strip()
    if not short:
        short = format_source_attempt_message(source, issue=issue)
    bag[source] = _PassVerdict(code=code, message=short, issue=issue)


def compose_download_pass_failure(row: object, source_order: list[str]) -> tuple[str, str] | None:
    bag: dict[str, _PassVerdict] = getattr(row, _PASS_ATTR, None) or {}
    if not bag:
        return None
    parts: list[str] = []
    primary_code = "NO_LINK"
    for name in source_order:
        verdict = bag.get(name)
        if verdict is None:
            continue
        if not parts:
            primary_code = verdict.code
        label = _source_label(name)
        detail = verdict.message.strip()
        if detail.startswith(f"{label} :"):
            parts.append(detail)
        else:
            parts.append(f"{label} : {detail}")
    if not parts:
        return None
    return primary_code, _SEPARATOR.join(parts)


def wawacity_had_no_stored_links(row: object) -> bool:
    verdict = (getattr(row, _PASS_ATTR, None) or {}).get("wawacity")
    return verdict is not None and verdict.issue == "empty" and verdict.code == "NO_LINK"

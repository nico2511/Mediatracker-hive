"""Durées de conservation fixes (non réglables en base)."""

from __future__ import annotations

from dataclasses import dataclass

EVENT_JOURNAL_DAYS = 30
SOURCE_STATE_DAYS = 30
METRICS_HISTORY_DAYS = 7
INCOMPLETE_ORPHAN_KEEP_DAYS = 3


@dataclass(frozen=True, slots=True)
class FixedRetention:
    event_journal_days: int
    source_state_days: int
    metrics_history_days: int
    incomplete_orphan_keep_days: int


def fixed_retention() -> FixedRetention:
    return FixedRetention(
        event_journal_days=EVENT_JOURNAL_DAYS,
        source_state_days=SOURCE_STATE_DAYS,
        metrics_history_days=METRICS_HISTORY_DAYS,
        incomplete_orphan_keep_days=INCOMPLETE_ORPHAN_KEEP_DAYS,
    )

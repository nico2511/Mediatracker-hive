"""Résultat de mise en file transfert Hive (catalogue / débloqueur)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HiveEnqueueResult:
    """``outcome`` : ``queued``, ``pending``, ``profile_missing`` ou ``miss``."""

    outcome: str
    detail: str | None = None

"""Pure profile-first then source attempt contract (opaque source identifiers)."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from mediatracker.core.quality_profile import Profile


@dataclass(frozen=True)
class ProfileSourceAttempt:
    profile_index: int
    profile: Profile
    source: str


def iter_profile_source_attempts(
    profiles: Sequence[Profile],
    source_order: Sequence[str],
) -> Iterator[ProfileSourceAttempt]:
    """Every profile is tried on every source before the next profile."""
    for profile_index, profile in enumerate(profiles):
        for source in source_order:
            yield ProfileSourceAttempt(profile_index, profile, source)


def walk_profile_source_attempts(
    profiles: Sequence[Profile],
    source_order: Sequence[str],
    *,
    accepts: Callable[[Profile, str], bool],
) -> tuple[ProfileSourceAttempt | None, tuple[ProfileSourceAttempt, ...]]:
    """First matching attempt and every attempt before it (or the full trail if none match)."""
    trail: list[ProfileSourceAttempt] = []
    for attempt in iter_profile_source_attempts(profiles, source_order):
        trail.append(attempt)
        if accepts(attempt.profile, attempt.source):
            return attempt, tuple(trail)
    return None, tuple(trail)

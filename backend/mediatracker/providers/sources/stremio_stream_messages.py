"""Libellés vides communs aux addons Stremio (Comet, Torrentio, addons, UwU+)."""

from __future__ import annotations

from typing import Any

from mediatracker.core.quality_profile import Profile


def stream_rows_seen(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    rows = payload.get("streams")
    if not isinstance(rows, list):
        return 0
    return len(rows)


def profile_mismatch_reason(profile: Profile, *, seen: int) -> str:
    plural = "flux" if seen != 1 else "flux"
    suffix = "s" if seen != 1 else ""
    return f"Aucun flux ne correspond au profil « {profile.name} » ({seen} {plural} vu{suffix})."


def addon_no_source_reason() -> str:
    return "Aucune source pour ce titre sur cet addon."
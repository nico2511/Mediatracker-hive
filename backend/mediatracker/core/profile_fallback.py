"""Ordered global quality-profile fallbacks and per-fiche resolution (lot A foundation).

Lot B/C adapters (``profile_attempt_adapters``) expose explicit single-profile paths for
remotes and Hive catalogue. Lot E (``profile_attempt_orchestration``) composes them with
this resolver for polls, Vérifier maintenant, Hive file hooks, and partie de la reingest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.quality_profile import Profile, profile_from_row
from mediatracker.db.models import QualityProfile, Setting

PROFILE_FALLBACK_ORDER_KEY = "profile_fallback_order"


class ProfileResolveError(ValueError):
    """Explicit resolver failure. Callers must not substitute another profile implicitly."""


@dataclass(frozen=True)
class ResolvedProfile:
    profile_id: int
    profile: Profile
    is_principal: bool


def parse_profile_fallback_order(raw: str) -> list[int]:
    """Comma-separated profile ids. Preserves user order; drops duplicate ids."""
    text = (raw or "").strip()
    if not text:
        return []
    found: list[int] = []
    for part in text.split(","):
        piece = part.strip()
        if not piece:
            continue
        if not piece.isdigit():
            raise ValueError(f"Identifiant de profil invalide : {piece}.")
        profile_id = int(piece)
        if profile_id <= 0:
            raise ValueError(f"Identifiant de profil invalide : {piece}.")
        if profile_id not in found:
            found.append(profile_id)
    return found


def format_profile_fallback_order(ids: Sequence[int]) -> str:
    return ",".join(str(profile_id) for profile_id in ids)


def read_profile_fallback_order(db: Session, settings: Settings | None = None) -> list[int]:
    """Absent row falls back to config; an explicit empty stored value stays empty."""
    row = db.get(Setting, PROFILE_FALLBACK_ORDER_KEY)
    if row is None:
        default = settings.profile_fallback_order if settings is not None else ""
        return parse_profile_fallback_order(default)
    return parse_profile_fallback_order(row.value or "")


def validate_profile_fallback_ids(db: Session, ids: Sequence[int]) -> None:
    from mediatracker.settings_store import SettingsError

    for profile_id in ids:
        if db.get(QualityProfile, int(profile_id)) is None:
            raise SettingsError(f"Profil de qualité inconnu : {profile_id}.")


def _principal_row(db: Session, media: object) -> tuple[int, QualityProfile]:
    pinned_id = getattr(media, "quality_profile_id", None)
    if pinned_id:
        row = db.get(QualityProfile, int(pinned_id))
        if row is None:
            raise ProfileResolveError(f"Profil épinglé introuvable (id {pinned_id}).")
        return int(pinned_id), row
    row = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)).limit(1))
    if row is None:
        raise ProfileResolveError("Aucun profil de qualité par défaut.")
    return int(row.id), row


def _chain_ids(principal_id: int, global_ids: Sequence[int], *, allow_fallback: bool) -> list[int]:
    chain = [principal_id]
    if not allow_fallback or not global_ids:
        return chain
    seen = {principal_id}
    for profile_id in global_ids:
        if profile_id in seen:
            continue
        seen.add(profile_id)
        chain.append(profile_id)
    return chain


def resolve_profiles_for_media(
    db: Session,
    media: object,
    settings: Settings | None = None,
) -> tuple[ResolvedProfile, ...]:
    """Principal first, then configured fallbacks when the fiche allows fallback."""
    principal_id, _row = _principal_row(db, media)
    global_ids = read_profile_fallback_order(db, settings)
    allow_fallback = bool(getattr(media, "allow_fallback", False))
    ids = _chain_ids(principal_id, global_ids, allow_fallback=allow_fallback)

    resolved: list[ResolvedProfile] = []
    for index, profile_id in enumerate(ids):
        row = db.get(QualityProfile, profile_id)
        if row is None:
            raise ProfileResolveError(f"Profil de qualité inconnu : {profile_id}.")
        resolved.append(
            ResolvedProfile(
                profile_id=profile_id,
                profile=profile_from_row(row),
                is_principal=index == 0,
            )
        )
    return tuple(resolved)

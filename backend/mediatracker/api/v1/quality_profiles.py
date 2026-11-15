"""CRUD for named quality profiles. One of them is the global default."""

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.quality_profile import (
    LANGUAGE_ALIASES,
    LANGUAGE_CODES,
    RESOLUTION_RANK,
    normalize_language,
    refresh_followed_media_for_profile_change,
)
from mediatracker.db.models import Download, Media, QualityProfile

router = APIRouter(prefix="/quality-profiles", tags=["quality-profiles"])


class QualityProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    languages: list[str] = Field(min_length=1, max_length=8)
    min_resolution: str | None = None
    max_resolution: str | None = None
    min_size_mb: int | None = Field(default=None, ge=1, le=200_000)
    max_size_mb: int | None = Field(default=None, ge=1, le=200_000)
    exclude_keywords: list[str] = Field(default_factory=list, max_length=30)
    is_default: bool = False


class QualityProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    languages: list[str] | None = Field(default=None, min_length=1, max_length=8)
    min_resolution: str | None = None
    max_resolution: str | None = None
    min_size_mb: int | None = Field(default=None, ge=1, le=200_000)
    max_size_mb: int | None = Field(default=None, ge=1, le=200_000)
    exclude_keywords: list[str] | None = Field(default=None, max_length=30)
    is_default: bool | None = None


class QualityProfileOut(BaseModel):
    id: int
    name: str
    languages: list[str]
    min_resolution: str | None
    max_resolution: str | None
    min_size_mb: int | None
    max_size_mb: int | None
    exclude_keywords: list[str]
    is_default: bool


class QualityProfileList(BaseModel):
    profiles: list[QualityProfileOut]


def _languages(values: list[str]) -> list[str]:
    found: list[str] = []
    for raw in values:
        code = normalize_language(raw.strip())
        token = raw.strip().upper()
        if code is None or (code not in LANGUAGE_CODES and token not in LANGUAGE_ALIASES):
            raise Problem(422, "Requête invalide", f"Langue inconnue : {raw.strip() or 'vide'}.")
        if code not in found:
            found.append(code)
    if not found:
        raise Problem(422, "Requête invalide", "Indiquez au moins une langue.")
    return found


def _resolution(value: str | None, label: str) -> str | None:
    if value is None or not str(value).strip():
        return None
    if value not in RESOLUTION_RANK:
        raise Problem(422, "Requête invalide", f"{label} inconnue.")
    return value


def _keywords(values: list[str]) -> list[str]:
    found: list[str] = []
    for raw in values:
        word = " ".join(raw.split())
        if not word:
            continue
        if len(word) > 40:
            raise Problem(422, "Requête invalide", "Un mot exclu dépasse 40 caractères.")
        if word.casefold() not in {item.casefold() for item in found}:
            found.append(word)
    return found


def _check_bounds(data: dict[str, Any]) -> None:
    low = data.get("min_resolution")
    high = data.get("max_resolution")
    if low and high and RESOLUTION_RANK[low] > RESOLUTION_RANK[high]:
        raise Problem(422, "Requête invalide", "La résolution minimale dépasse la maximale.")
    small = data.get("min_size_mb")
    large = data.get("max_size_mb")
    if small is not None and large is not None and small > large:
        raise Problem(422, "Requête invalide", "La taille minimale dépasse la maximale.")


def _apply(data: dict[str, Any]) -> dict[str, Any]:
    if "name" in data and data["name"] is not None:
        data["name"] = " ".join(str(data["name"]).split())
        if not data["name"]:
            raise Problem(422, "Requête invalide", "Le nom du profil est vide.")
    if "languages" in data and data["languages"] is not None:
        data["languages"] = _languages(list(data["languages"]))
    if "min_resolution" in data:
        data["min_resolution"] = _resolution(data["min_resolution"], "Résolution minimale")
    if "max_resolution" in data:
        data["max_resolution"] = _resolution(data["max_resolution"], "Résolution maximale")
    if "exclude_keywords" in data and data["exclude_keywords"] is not None:
        data["exclude_keywords"] = _keywords(list(data["exclude_keywords"]))
    return data


def _out(row: QualityProfile) -> QualityProfileOut:
    return QualityProfileOut(
        id=row.id,
        name=row.name,
        languages=list(row.languages or []),
        min_resolution=row.min_resolution,
        max_resolution=row.max_resolution,
        min_size_mb=row.min_size_mb,
        max_size_mb=row.max_size_mb,
        exclude_keywords=list(row.exclude_keywords or []),
        is_default=bool(row.is_default),
    )


def _get(db: Session, profile_id: int) -> QualityProfile:
    row = db.get(QualityProfile, profile_id)
    if row is None:
        raise Problem(404, "Introuvable", "Profil de qualité introuvable.")
    return row


def _set_default(db: Session, row: QualityProfile) -> None:
    others = db.scalars(select(QualityProfile).where(QualityProfile.is_default.is_(True))).all()
    for other in others:
        if other.id != row.id:
            other.is_default = False
    row.is_default = True


@router.get("", response_model=QualityProfileList)
def list_profiles(
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> QualityProfileList:
    del _principal
    order = select(QualityProfile).order_by(QualityProfile.is_default.desc(), QualityProfile.name)
    rows = list(db.scalars(order).all())
    return QualityProfileList(profiles=[_out(row) for row in rows])


@router.post("", response_model=QualityProfileOut, status_code=201)
def create_profile(
    body: QualityProfileIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> QualityProfileOut:
    del _principal
    data = _apply(body.model_dump())
    _check_bounds(data)
    row = QualityProfile(
        name=data["name"],
        languages=data["languages"],
        min_resolution=data["min_resolution"],
        max_resolution=data["max_resolution"],
        min_size_mb=data["min_size_mb"],
        max_size_mb=data["max_size_mb"],
        exclude_keywords=data["exclude_keywords"],
        is_default=False,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", "Ce nom est déjà utilisé.") from exc
    if data["is_default"] or db.scalar(select(QualityProfile.id).where(QualityProfile.is_default.is_(True))) is None:
        _set_default(db, row)
    db.commit()
    db.refresh(row)
    return _out(row)


@router.patch("/{profile_id}", response_model=QualityProfileOut)
def update_profile(
    profile_id: int,
    body: QualityProfilePatch,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> QualityProfileOut:
    del _principal
    row = _get(db, profile_id)
    was_default = bool(row.is_default)
    data = _apply(body.model_dump(exclude_unset=True))
    profile_fields = {
        "name",
        "languages",
        "min_resolution",
        "max_resolution",
        "min_size_mb",
        "max_size_mb",
        "exclude_keywords",
    }
    touches_schedule = bool(profile_fields.intersection(data))
    merged = {
        "min_resolution": data.get("min_resolution", row.min_resolution),
        "max_resolution": data.get("max_resolution", row.max_resolution),
        "min_size_mb": data.get("min_size_mb", row.min_size_mb),
        "max_size_mb": data.get("max_size_mb", row.max_size_mb),
    }
    _check_bounds(merged)
    becoming_default = data.get("is_default") is True
    clearing_default = data.get("is_default") is False and row.is_default
    if clearing_default:
        raise Problem(422, "Requête invalide", "Choisissez un autre profil par défaut avant de retirer celui-ci.")
    data.pop("is_default", None)
    for key, value in data.items():
        setattr(row, key, value)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", "Ce nom est déjà utilisé.") from exc
    if becoming_default:
        _set_default(db, row)
    if touches_schedule or becoming_default:
        moment = datetime.now(timezone.utc)
        refresh_followed_media_for_profile_change(
            db,
            row.id,
            affects_default=was_default or bool(row.is_default),
            now=moment,
        )
    db.commit()
    db.refresh(row)
    return _out(row)


@router.delete("/{profile_id}", status_code=204)
def delete_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    row = _get(db, profile_id)
    if row.is_default:
        raise Problem(
            422,
            "Requête invalide",
            "Définissez un autre profil par défaut avant de supprimer celui-ci.",
        )
    for media in db.scalars(select(Media).where(Media.quality_profile_id == row.id)).all():
        media.quality_profile_id = None
    in_queue = db.scalar(
        select(func.count()).select_from(Download).where(Download.poll_quality_profile_id == row.id)
    )
    if in_queue:
        raise Problem(
            422,
            "Requête invalide",
            "Ce profil est référencé par au moins un téléchargement issu d'un sondage.",
        )
    from mediatracker.core.profile_fallback import purge_profile_id_from_fallback_references

    purge_profile_id_from_fallback_references(db, row.id)
    db.delete(row)
    db.commit()

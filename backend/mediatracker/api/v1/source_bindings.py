"""Read, edit, and probe Wawacity page bindings for a fiche or season."""

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.api.v1.media import _get_media
from mediatracker.core.source_binding import (
    binding_page_url,
    get_binding,
    probe_binding_page,
    resolve_binding_path,
    serialize_binding,
    upsert_binding_path,
    wawacity_provider_id,
)
from mediatracker.db.models import Media

router = APIRouter(tags=["media"])


class SourceBindingOut(BaseModel):
    url: str | None
    path: str | None
    state: str
    last_error: str | None
    last_error_at: datetime | None
    last_scanned_at: datetime | None
    manual: bool


class SourceBindingUpdate(BaseModel):
    url: str | None = Field(default=None, max_length=2000)
    path: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _has_input(self) -> "SourceBindingUpdate":
        if not (self.url or "").strip() and not (self.path or "").strip():
            raise ValueError("Indiquez une URL complète ou un chemin relatif.")
        return self


class SourceBindingTestOut(BaseModel):
    accessible: bool
    title: str | None
    links_by_hoster: dict[str, int]
    message: str | None = None


def _film_binding(
    db: Session,
    settings,
    media: Media,
) -> SourceBindingOut:
    if media.type != "film":
        raise Problem(422, "Requête invalide", "Utilisez la liaison de saison pour une série.")
    provider_id = wawacity_provider_id(db)
    row = get_binding(db, media.id, provider_id, None)
    return SourceBindingOut(**serialize_binding(db, settings, row))


def _season_binding(
    db: Session,
    settings,
    media: Media,
    number: int,
) -> SourceBindingOut:
    if media.type != "series":
        raise Problem(422, "Requête invalide", "Un film n'a pas de saison.")
    if number < 0:
        raise Problem(422, "Requête invalide", "Numéro de saison invalide.")
    provider_id = wawacity_provider_id(db)
    row = get_binding(db, media.id, provider_id, number)
    return SourceBindingOut(**serialize_binding(db, settings, row))


def _apply_update(
    db: Session,
    settings,
    media: Media,
    *,
    season: int | None,
    body: SourceBindingUpdate,
) -> SourceBindingOut:
    try:
        stored = resolve_binding_path(db, settings, url=body.url, path=body.path)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc
    row = upsert_binding_path(
        db,
        settings,
        media,
        season=season,
        stored_path=stored,
    )
    db.commit()
    db.refresh(row)
    return SourceBindingOut(**serialize_binding(db, settings, row))


def _run_test(
    request: Request,
    db: Session,
    media: Media,
    *,
    season: int | None,
) -> SourceBindingTestOut:
    settings = request.app.state.settings
    try:
        provider_id = wawacity_provider_id(db)
    except ValueError as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc
    row = get_binding(db, media.id, provider_id, season)
    if row is None or not (row.path or row.url):
        raise Problem(422, "Requête invalide", "Aucune liaison source à tester.")
    page_url = binding_page_url(db, settings, row)
    if not page_url:
        raise Problem(422, "Requête invalide", "La liaison n'a pas d'URL exploitable.")
    transport = getattr(request.app.state, "wawacity_test_transport", None)
    result = probe_binding_page(db, settings, page_url, transport=transport)
    return SourceBindingTestOut(
        accessible=result.accessible,
        title=result.title,
        links_by_hoster=result.links_by_hoster,
        message=result.message,
    )


@router.get("/{media_id}/source-binding", response_model=SourceBindingOut)
def get_film_source_binding(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SourceBindingOut:
    del _principal
    media = _get_media(db, media_id)
    return _film_binding(db, request.app.state.settings, media)


@router.put("/{media_id}/source-binding", response_model=SourceBindingOut)
@router.patch("/{media_id}/source-binding", response_model=SourceBindingOut)
def update_film_source_binding(
    media_id: int,
    body: SourceBindingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> SourceBindingOut:
    del _principal
    media = _get_media(db, media_id)
    if media.type != "film":
        raise Problem(422, "Requête invalide", "Utilisez la liaison de saison pour une série.")
    return _apply_update(db, request.app.state.settings, media, season=None, body=body)


@router.post("/{media_id}/source-binding/test", response_model=SourceBindingTestOut)
def test_film_source_binding(
    media_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> SourceBindingTestOut:
    del _principal
    media = _get_media(db, media_id)
    if media.type != "film":
        raise Problem(422, "Requête invalide", "Utilisez le test de liaison de saison pour une série.")
    return _run_test(request, db, media, season=None)


@router.get("/{media_id}/seasons/{number}/source-binding", response_model=SourceBindingOut)
def get_season_source_binding(
    media_id: int,
    number: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> SourceBindingOut:
    del _principal
    media = _get_media(db, media_id)
    return _season_binding(db, request.app.state.settings, media, number)


@router.put("/{media_id}/seasons/{number}/source-binding", response_model=SourceBindingOut)
@router.patch("/{media_id}/seasons/{number}/source-binding", response_model=SourceBindingOut)
def update_season_source_binding(
    media_id: int,
    number: int,
    body: SourceBindingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> SourceBindingOut:
    del _principal
    media = _get_media(db, media_id)
    if media.type != "series":
        raise Problem(422, "Requête invalide", "Un film n'a pas de saison.")
    return _apply_update(db, request.app.state.settings, media, season=number, body=body)


@router.post("/{media_id}/seasons/{number}/source-binding/test", response_model=SourceBindingTestOut)
def test_season_source_binding(
    media_id: int,
    number: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> SourceBindingTestOut:
    del _principal
    media = _get_media(db, media_id)
    if media.type != "series":
        raise Problem(422, "Requête invalide", "Un film n'a pas de saison.")
    return _run_test(request, db, media, season=number)

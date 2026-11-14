"""TMDB image proxy with a on-disk cache."""

from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from fastapi.responses import Response as PlainResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.display_title import clean_display_title
from mediatracker.core.source_poster_cache import (
    cache_control_header,
    resolve_cached_source_poster,
)
from mediatracker.db.models import CatalogEntry
from mediatracker.integrations.tmdb_images import (
    _CACHE_CONTROL,
    ImageCacheError,
    _etag,
    _media_type,
    cache_file,
    cache_root,
    purge_cache,
    resolve_cached,
    touch_if_stale,
    validate_filename,
    validate_kind,
    validate_size,
)

router = APIRouter(prefix="/images", tags=["images"])


class PurgeOut(BaseModel):
    removed_stale: int
    removed_lru: int
    bytes_freed: int = Field(description="Octets libérés sur le disque.")


_TITLE_CARD_CONTROL = "no-store"


@router.get("/source/{entry_id}", response_model=None)
def catalog_source_poster(
    entry_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
):
    row = db.get(CatalogEntry, entry_id)
    if row is None:
        raise Problem(404, "Image", "Vignette introuvable.")
    settings = request.app.state.settings
    cached = resolve_cached_source_poster(db, settings, row)
    if cached is None:
        del _principal
        return catalog_title_card(entry_id=entry_id, db=db, _principal=None)
    del _principal
    path, media_type, etag = cached
    cache_control = cache_control_header()
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": cache_control, "ETag": etag},
    )


@router.get("/title-card/{entry_id}")
def catalog_title_card(
    entry_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> PlainResponse:
    del _principal
    row = db.get(CatalogEntry, entry_id)
    if row is None:
        raise Problem(404, "Image", "Vignette introuvable.")
    raw = (clean_display_title(row.title) or row.title or "Média")[:80]
    label = escape(raw, {'"': '&quot;', "'": '&apos;'})
    text = escape(raw)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="342" height="513" '
        f'viewBox="0 0 342 513" role="img" aria-label="{label}">'
        '<rect width="100%" height="100%" fill="#1a1d24"/>'
        '<text x="50%" y="50%" fill="#9aa3b2" font-family="system-ui,sans-serif" '
        'font-size="22" text-anchor="middle" dominant-baseline="middle">'
        f'<tspan x="50%" dy="0">{text}</tspan></text></svg>'
    )
    return PlainResponse(
        content=svg,
        media_type="image/svg+xml",
        headers={"Cache-Control": _TITLE_CARD_CONTROL},
    )


@router.get("/{kind}/{size}/{filename}")
def get_image(
    kind: str,
    size: str,
    filename: str,
    request: Request,
    _principal=Depends(require_scope("read")),
):
    del _principal
    settings = request.app.state.settings
    try:
        kind = validate_kind(kind)
        size = validate_size(size)
        filename = validate_filename(filename)
    except ImageCacheError as exc:
        raise Problem(404, "Image", exc.message) from exc
    dest = cache_file(cache_root(settings), kind, size, filename)
    if dest.is_file():
        etag = _etag(dest)
        if request.headers.get("if-none-match") == etag:
            touch_if_stale(dest)
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL})
        touch_if_stale(dest)
        return FileResponse(
            dest,
            media_type=_media_type(filename),
            headers={"Cache-Control": _CACHE_CONTROL, "ETag": etag},
        )
    try:
        cached = resolve_cached(settings, kind, size, filename, touch=True)
    except ImageCacheError as exc:
        status = 404 if exc.code in {"NOT_FOUND", "BAD_NAME", "BAD_KIND", "BAD_SIZE"} else 502
        raise Problem(status, "Image", exc.message) from exc
    return FileResponse(
        cached.path,
        media_type=cached.media_type,
        headers={"Cache-Control": _CACHE_CONTROL, "ETag": cached.etag},
    )


@router.post("/purge", response_model=PurgeOut)
def purge_images(
    request: Request,
    _principal=Depends(require_scope("write")),
) -> PurgeOut:
    """Purge le cache d'affiches TMDB (admin / intégration, sans écran dédié dans l'UI)."""
    del _principal
    report = purge_cache(request.app.state.settings)
    return PurgeOut(
        removed_stale=report.removed_stale,
        removed_lru=report.removed_lru,
        bytes_freed=report.bytes_freed,
    )

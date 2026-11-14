"""TMDB search. Following a result is ``POST /media`` with the returned id."""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import LibraryFile, Media
from mediatracker.integrations.tmdb import TmdbClient, TmdbError, borrow_tmdb

router = APIRouter(prefix="/metadata/tmdb", tags=["metadata"])


class TmdbSeasonOut(BaseModel):
    number: int
    name: str | None = None
    episode_count: int | None = None


class TmdbHitOut(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    year: int | None
    overview: str | None
    poster_url: str | None
    original_title: str | None = None
    season_count: int | None = None
    episode_count: int | None = None
    runtime: int | None = None
    followed: bool = False
    in_library: bool = False
    seasons: list[TmdbSeasonOut] = Field(default_factory=list)


class TmdbSearchOut(BaseModel):
    items: list[TmdbHitOut]


@router.get("/search", response_model=TmdbSearchOut)
def search_tmdb(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    q: str = Query(min_length=2, max_length=200),
    type: str = Query(default="multi", pattern="^(film|series|multi)$"),
    detail: bool = Query(default=False),
) -> TmdbSearchOut:
    del _principal
    token = tmdb_token.read_tmdb_token(db, request.app.state.settings.secret_key)
    if not token:
        raise Problem(422, "TMDB", "Renseignez le jeton TMDB dans les réglages pour chercher un titre.")
    client, owned = borrow_tmdb(token, TmdbClient)
    try:
        hits = client.search_multi(q.strip()) if type == "multi" else client.search(q.strip(), type)
    except TmdbError as exc:
        status = 422 if exc.code == "UNAUTHORIZED" else 502
        raise Problem(status, "TMDB", exc.message) from exc
    finally:
        if owned:
            client.close()
    if detail:
        hits = _with_detail(token, hits[:8]) + hits[8:]
    followed = {
        (row.type, row.tmdb_id): row.followed
        for row in db.scalars(select(Media).where(Media.tmdb_id.is_not(None))).all()
    }
    library_ids = {
        row.tmdb_id
        for row in db.scalars(select(LibraryFile).where(LibraryFile.tmdb_id.is_not(None))).all()
        if row.tmdb_id is not None
    }
    return TmdbSearchOut(
        items=[
            TmdbHitOut(
                tmdb_id=hit.tmdb_id,
                media_type=hit.media_type,
                title=hit.title,
                year=hit.year,
                overview=hit.overview,
                poster_url=hit.poster_url,
                original_title=hit.original_title,
                season_count=hit.season_count,
                episode_count=hit.episode_count,
                runtime=hit.runtime,
                followed=bool(followed.get((hit.media_type, hit.tmdb_id))),
                in_library=hit.tmdb_id in library_ids,
                seasons=_seasons(hit),
            )
            for hit in hits[:20]
        ]
    )


def _with_detail(token: str, hits: list):
    client, owned = borrow_tmdb(token, TmdbClient)
    detailed = []
    try:
        for hit in hits:
            try:
                detailed.append(client.glance(hit.tmdb_id, hit.media_type))
            except TmdbError:
                detailed.append(hit)
    finally:
        if owned:
            client.close()
    return detailed


@router.get("/{media_type}/{tmdb_id}", response_model=TmdbHitOut)
def read_tmdb_title(
    media_type: str,
    tmdb_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> TmdbHitOut:
    """Preview one TMDB id before it is applied to a library folder."""
    del _principal
    if media_type not in {"film", "series"}:
        raise Problem(422, "Requête invalide", "Type attendu : film ou series.")
    token = tmdb_token.read_tmdb_token(db, request.app.state.settings.secret_key)
    if not token:
        raise Problem(422, "TMDB", "Renseignez le jeton TMDB dans les réglages.")
    client, owned = borrow_tmdb(token, TmdbClient)
    try:
        hit = client.glance(tmdb_id, media_type)
    except TmdbError as exc:
        status = 422 if exc.code == "UNAUTHORIZED" else 404 if exc.code == "NOT_FOUND" else 502
        raise Problem(status, "TMDB", exc.message) from exc
    finally:
        if owned:
            client.close()
    return TmdbHitOut(
        tmdb_id=hit.tmdb_id,
        media_type=hit.media_type,
        title=hit.title,
        year=hit.year,
        overview=hit.overview,
        poster_url=hit.poster_url,
        original_title=hit.original_title,
        season_count=hit.season_count,
        episode_count=hit.episode_count,
        runtime=hit.runtime,
        seasons=_seasons(hit),
    )


def _seasons(hit) -> list[TmdbSeasonOut]:
    return [
        TmdbSeasonOut(number=season.number, name=season.name, episode_count=season.episode_count)
        for season in getattr(hit, "seasons", ())
    ]

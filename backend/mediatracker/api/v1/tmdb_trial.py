"""Trial pages fed only by TMDB (no scraped catalog)."""

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.api.v1.dashboard import LatestCard
from mediatracker.core.tmdb_trial import browse_page, home_rows, providers_fr
from mediatracker.hive.home_row import list_hive_dispos_for_home
from mediatracker.integrations.tmdb import TmdbError

router = APIRouter(prefix="/tmdb-trial", tags=["tmdb-trial"])


class TmdbTrialCard(BaseModel):
    tmdb_id: int
    media_type: str
    title: str
    year: int | None = None
    release_date: str | None = None
    overview: str | None = None
    poster_url: str | None = None
    vote_average: float | None = None
    subtitle: str | None = None
    followed: bool = False
    in_library: bool = False
    media_id: int | None = None
    tmdb_url: str | None = None


class TmdbTrialHome(BaseModel):
    cinema_films: list[TmdbTrialCard] = Field(default_factory=list)
    streaming_films: list[TmdbTrialCard] = Field(default_factory=list)
    physical_films: list[TmdbTrialCard] = Field(default_factory=list)
    new_series: list[TmdbTrialCard] = Field(default_factory=list)
    on_air_series: list[TmdbTrialCard] = Field(default_factory=list)
    hive_dispos: list[LatestCard] = Field(default_factory=list)


class TmdbTrialBrowsePage(BaseModel):
    items: list[TmdbTrialCard]
    total: int
    page: int
    genres: list[str] = Field(default_factory=list)
    years: list[int] = Field(default_factory=list)
    providers: list[dict[str, int | str]] = Field(default_factory=list)


def _problem(exc: TmdbError) -> Problem:
    status = 422 if exc.code == "UNAUTHORIZED" else 502
    return Problem(status, "TMDB", exc.message)


@router.get("/home", response_model=TmdbTrialHome)
def get_tmdb_trial_home(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> TmdbTrialHome:
    del _principal
    settings = request.app.state.settings
    try:
        sections = home_rows(db, settings)
    except TmdbError as exc:
        raise _problem(exc) from exc
    hive = [LatestCard.model_validate(row) for row in list_hive_dispos_for_home(db, settings, limit=20)]
    return TmdbTrialHome(
        hive_dispos=hive,
        **{key: [TmdbTrialCard.model_validate(row) for row in value] for key, value in sections.items()},
    )


@router.get("/browse", response_model=TmdbTrialBrowsePage)
def browse_tmdb_trial(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
    media_type: str = Query(alias="type", pattern="^(film|series)$"),
    page: int = Query(default=1, ge=1, le=50),
    limit: int = Query(default=60, ge=1, le=60),
    release_kind: str | None = Query(
        default=None,
        pattern="^(cinema|streaming|physical|new|on_air)$",
    ),
    provider: int | None = Query(default=None, ge=1),
    genre: str | None = None,
    year: int | None = Query(default=None, ge=1880, le=2100),
    q: str | None = Query(default=None, max_length=200),
    followed: bool | None = None,
    in_library: bool | None = None,
    sort: str = Query(default="recent", pattern="^(recent|popularity)$"),
) -> TmdbTrialBrowsePage:
    del _principal
    settings = request.app.state.settings
    try:
        items, total, genres, years = browse_page(
            db,
            settings,
            media_type=media_type,
            page=page,
            limit=limit,
            release_kind=release_kind,
            provider_id=provider,
            genre=genre,
            year=year,
            query=q,
            followed=followed,
            in_library=in_library,
            sort_mode=sort,
        )
        provider_rows = providers_fr(db, settings) if media_type == "film" else []
    except TmdbError as exc:
        raise _problem(exc) from exc
    return TmdbTrialBrowsePage(
        items=[TmdbTrialCard.model_validate(row) for row in items],
        total=total,
        page=page,
        genres=genres,
        years=years,
        providers=provider_rows,
    )

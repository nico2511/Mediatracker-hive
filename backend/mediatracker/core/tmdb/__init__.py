"""TMDB service: identification, token, fetch, and apply."""

from mediatracker.core.tmdb.apply import apply_tmdb
from mediatracker.core.tmdb.client import open_client
from mediatracker.core.tmdb.create import create_from_tmdb
from mediatracker.core.tmdb.fetch import fetch_title
from mediatracker.core.tmdb.identify import (
    IdentifyResult,
    candidate_payload,
    exact_hit,
    glance_hit,
    hit_years_differ,
    identify,
    identify_search,
)
from mediatracker.core.tmdb.imdb import ensure_imdb
from mediatracker.core.tmdb.token import read_tmdb_token

__all__ = [
    "IdentifyResult",
    "apply_tmdb",
    "candidate_payload",
    "create_from_tmdb",
    "ensure_imdb",
    "exact_hit",
    "fetch_title",
    "glance_hit",
    "hit_years_differ",
    "identify",
    "identify_search",
    "open_client",
    "read_tmdb_token",
]

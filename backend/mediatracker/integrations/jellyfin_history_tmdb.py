"""Compatibilité : recherche TMDB pour le CSV de correction."""

from __future__ import annotations

_read_tmdb_token = None
_resolve_exact_tmdb_candidate = None
TmdbExportRateLimited = None
TmdbCandidate = None
SearchHook = None


def bind_core_tmdb(
    *,
    read_tmdb_token,
    resolve_exact_tmdb_candidate,
    tmdb_export_rate_limited,
    tmdb_candidate,
    search_hook,
):
    global _read_tmdb_token, _resolve_exact_tmdb_candidate
    global TmdbExportRateLimited, TmdbCandidate, SearchHook, search_first_tmdb_candidate
    _read_tmdb_token = read_tmdb_token
    _resolve_exact_tmdb_candidate = resolve_exact_tmdb_candidate
    TmdbExportRateLimited = tmdb_export_rate_limited
    TmdbCandidate = tmdb_candidate
    SearchHook = search_hook
    search_first_tmdb_candidate = resolve_exact_tmdb_candidate


search_first_tmdb_candidate = None  # set after bind


def _ensure_bound():
    if _resolve_exact_tmdb_candidate is None or _read_tmdb_token is None:
        raise RuntimeError("Jellyfin TMDB correction wiring missing (wire_jellyfin_integrations)")


def resolve_exact_tmdb_candidate(
    db,
    settings,
    secret_key: str,
    *,
    query: str,
    media_type: str,
    year: int | None = None,
    cache=None,
    search_hook=None,
    client=None,
):
    del settings
    _ensure_bound()
    from mediatracker.integrations.tmdb import TmdbClient, borrow_tmdb

    owned = False
    if client is None and search_hook is None and db is not None and secret_key:
        token = _read_tmdb_token(db, secret_key)
        if token:
            client, owned = borrow_tmdb(token, TmdbClient)
    try:
        return _resolve_exact_tmdb_candidate(
            query=query,
            media_type=media_type,
            year=year,
            cache=cache,
            search_hook=search_hook,
            client=client,
        )
    finally:
        if owned and client is not None:
            client.close()

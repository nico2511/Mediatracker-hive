"""Bind core TMDB services into Jellyfin integration modules (layering)."""

from __future__ import annotations

_wired = False


def _read_tmdb_token(db, secret_key: str) -> str:
    from mediatracker.core.tmdb.token import read_tmdb_token

    return read_tmdb_token(db, secret_key)


def _create_from_tmdb(*args, **kwargs):
    from mediatracker.core.tmdb.create import create_from_tmdb

    return create_from_tmdb(*args, **kwargs)


def _fetch_title(*args, **kwargs):
    from mediatracker.core.tmdb.fetch import fetch_title

    return fetch_title(*args, **kwargs)


def _identify(*args, **kwargs):
    from mediatracker.core.tmdb.identify import identify

    return identify(*args, **kwargs)


def _resolve_exact(*args, **kwargs):
    from mediatracker.core.tmdb.correction import resolve_exact_tmdb_candidate

    return resolve_exact_tmdb_candidate(*args, **kwargs)


def wire_jellyfin_integrations() -> None:
    """Call once at app startup; safe to call repeatedly."""
    global _wired
    if _wired:
        return
    from mediatracker.core.tmdb.correction import SearchHook, TmdbCandidate, TmdbExportRateLimited
    from mediatracker.integrations import jellyfin_db_export, jellyfin_history_import, jellyfin_history_tmdb

    jellyfin_history_import.bind_core_tmdb(
        read_tmdb_token=_read_tmdb_token,
        create_from_tmdb=_create_from_tmdb,
        fetch_title=_fetch_title,
        identify=_identify,
    )
    jellyfin_db_export.bind_core_tmdb(
        read_tmdb_token=_read_tmdb_token,
        resolve_exact_tmdb_candidate=_resolve_exact,
        tmdb_export_rate_limited=TmdbExportRateLimited,
    )
    jellyfin_history_tmdb.bind_core_tmdb(
        read_tmdb_token=_read_tmdb_token,
        resolve_exact_tmdb_candidate=_resolve_exact,
        tmdb_export_rate_limited=TmdbExportRateLimited,
        tmdb_candidate=TmdbCandidate,
        search_hook=SearchHook,
    )
    _wired = True

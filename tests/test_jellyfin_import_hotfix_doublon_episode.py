"""Hotfix import Jellyfin : doublon d'épisode (uq_media_item_slot) et compteur de fiches à créer."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.jellyfin_db_export import rows_to_reimport_csv_text
from mediatracker.integrations.jellyfin_history_import import (
    HistoryLine,
    ImportPreview,
    LineOutcome,
    apply_import,
    parse_csv_text,
    preview_import,
    preview_to_dict,
)
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbSeason, TmdbTitle

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
SERIES_TMDB = 990046


def _csv_row(**overrides) -> dict[str, str]:
    row = {
        "type": "episode",
        "title": "",
        "year": "",
        "series_title": "Serie Fictive Doublon",
        "season": "1",
        "episode": "1",
        "tmdb_id": str(SERIES_TMDB),
        "imdb_id": "",
        "tvdb_id": "",
        "jellyfin_item_id": "",
        "watched_at": "2024-01-01T12:00:00Z",
        "play_count": "1",
        "source": "userdata",
        "raison": "",
        "drapeaux": "",
        "confiance": "sur",
    }
    row.update(overrides)
    return row


def _fake_series() -> TmdbTitle:
    episodes = (
        TmdbEpisode(1, 1, "Episode Fictif Un", date(2020, 1, 1), 9900461),
        TmdbEpisode(1, 2, "Episode Fictif Deux", date(2020, 1, 8), 9900462),
    )
    season = TmdbSeason(1, "Saison 1", None, date(2020, 1, 1), 2, 99004601, episodes)
    return TmdbTitle(
        SERIES_TMDB, "series", "Serie Fictive Doublon", 2020, None, None, "Ended", date(2020, 1, 1), (season,)
    )


def test_unknown_series_with_tmdb_episodes_does_not_duplicate_slot(client, app, monkeypatch):
    """Série inconnue + épisodes TMDB en attente : pas d'IntegrityError, un seul S1E1 marqué vu."""
    del client
    calls: list[tuple[str, int]] = []

    def fake_maybe_fetch(
        db, settings, secret_key, tmdb_id, media_type, *, tmdb_cache=None, import_tx=None, **kwargs
    ):
        del import_tx
        del db, settings, secret_key, tmdb_cache
        calls.append((media_type, tmdb_id))
        if media_type == "series" and tmdb_id == SERIES_TMDB:
            return _fake_series()
        return None

    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_history_import._maybe_fetch",
        fake_maybe_fetch,
    )
    csv_text = rows_to_reimport_csv_text(
        [
            _csv_row(title=""),
            _csv_row(title="", watched_at="2024-01-02T12:00:00Z"),
            _csv_row(title="Episode Fictif Un", watched_at="2024-01-03T12:00:00Z"),
        ]
    )
    lines = parse_csv_text(csv_text)
    assert lines[0].title == "Sans titre"

    db = app.state.session_factory()
    settings = app.state.settings
    try:
        result = apply_import(db, settings, settings.secret_key, lines, now=NOW)
        db.commit()
        assert calls, "TMDB (mocké) doit être sollicité"
        series = db.scalars(select(Media).where(Media.tmdb_id == SERIES_TMDB)).all()
        assert len(series) == 1
        slots = db.scalars(
            select(MediaItem).where(
                MediaItem.media_id == series[0].id,
                MediaItem.season == 1,
                MediaItem.episode == 1,
            )
        ).all()
        assert len(slots) == 1
        assert slots[0].watched is True
        assert slots[0].title == "Episode Fictif Un"
        assert result.created_media == 1
        assert not result.preview.unmatched
    finally:
        db.close()


def _line(no: int, row_type: str, **kw) -> HistoryLine:
    base = dict(
        line_no=no,
        row_type=row_type,
        title=kw.pop("title", f"Titre {no}"),
        year=None,
        series_title=None,
        season=None,
        episode=None,
        tmdb_id=None,
        imdb_id=None,
        tvdb_id=None,
        jellyfin_item_id=None,
        watched_at=NOW,
        play_count=1,
        source="userdata",
    )
    base.update(kw)
    return HistoryLine(**base)


def test_preview_counts_distinct_media_to_create():
    lines = []
    no = 2
    for season, episode in [(1, 1), (1, 2), (1, 3), (2, 1), (2, 2)]:
        lines.append(_line(no, "episode", series_title="Serie Fictive Alpha", season=season, episode=episode))
        no += 1
    for episode in range(1, 6):
        lines.append(_line(no, "episode", series_title=None, tvdb_id=880011, season=1, episode=episode))
        no += 1
    lines.append(_line(no, "movie", title="Film Fictif Un", year=2001, tmdb_id=770001))
    lines.append(_line(no + 1, "movie", title="Film Fictif Deux", year=2002, imdb_id="tt7700002"))
    lines.append(_line(no + 2, "movie", title="Film Fictif Trois", year=2003))
    # Deuxième visionnage du même film : pas une nouvelle fiche.
    lines.append(_line(no + 3, "movie", title="Film Fictif Un", year=2001, tmdb_id=770001))
    preview = ImportPreview(line_count=len(lines))
    preview.to_create = [LineOutcome(line=line, status="create", create_media=True) for line in lines]

    body = preview_to_dict(preview)

    assert body["create_count"] == 14
    assert body["create_media_count"] == 5
    assert body["create_series_count"] == 2
    assert body["create_film_count"] == 3


def test_preview_media_count_merges_lines_sharing_a_provider_id():
    lines = [
        _line(2, "movie", title="Film Fictif Lie", year=2010, tmdb_id=770010),
        _line(3, "movie", title="Film Fictif Lie", year=2010, tmdb_id=770010, imdb_id="tt7700010"),
        _line(4, "movie", title="Film Fictif Lie", year=2010, imdb_id="tt7700010"),
        _line(5, "movie", title="Film Fictif Homonyme", year=2010),
        _line(6, "movie", title="film fictif homonyme", year=2011),
        _line(7, "episode", series_title="Serie Fictive Beta", tvdb_id=880020, season=1, episode=1),
        _line(8, "episode", series_title="Serie Fictive Beta", tvdb_id=880020, tmdb_id=660020, season=1, episode=2),
    ]
    preview = ImportPreview(line_count=len(lines))
    preview.to_create = [LineOutcome(line=line, status="create", create_media=True) for line in lines]

    body = preview_to_dict(preview)

    assert body["create_film_count"] == 3
    assert body["create_series_count"] == 1
    assert body["create_media_count"] == 4


def test_preview_import_media_count_on_database(client, app):
    del client
    db = app.state.session_factory()
    try:
        lines = [
            _line(2, "episode", series_title="Serie Fictive Gamma", season=1, episode=1),
            _line(3, "episode", series_title="Serie Fictive Gamma", season=1, episode=2),
            _line(4, "movie", title="Film Fictif Gamma", year=2015),
        ]
        body = preview_to_dict(preview_import(db, lines))
        assert body["create_count"] == 3
        assert body["create_media_count"] == 2
        assert body["create_series_count"] == 1
        assert body["create_film_count"] == 1
    finally:
        db.close()

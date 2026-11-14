"""Tests PR2 reprise : identify_search, rattrapage, enrichissement, parsing TMDB."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from tests.conftest import csrf, login

from mediatracker.core.seasons import (
    _IDENTIFY_BACKOFF_KEY,
    _IDENTIFY_CURSOR,
    maybe_refresh_films,
    maybe_refresh_unfollowed_enrichment,
    maybe_retry_pending_identify,
)
from mediatracker.core.tmdb.identify import identify_search
from mediatracker.db.models import Media, Setting
from mediatracker.integrations.tmdb import TmdbError, TmdbHit, TmdbTitle
from mediatracker.integrations.tmdb_enrichment import detail_enrichment_from_row, providers_fr_from_row

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


class _SearchOnly:
    def __init__(self, hits: list[TmdbHit]) -> None:
        self.hits = hits
        self.glance_calls = 0

    def search(self, query: str, media_type: str):
        del query, media_type
        return self.hits

    def glance(self, tmdb_id: int, media_type: str):
        del tmdb_id, media_type
        self.glance_calls += 1
        raise AssertionError("glance should not run")


def test_identify_search_exact_without_glance():
    client = _SearchOnly([TmdbHit(2, "film", "Exemple Alpha", 2020, None, None)])
    outcome = identify_search(client, "Exemple Alpha", 2020, "film", with_glance=False)
    assert outcome.hit is not None
    assert outcome.hit.tmdb_id == 2
    assert client.glance_calls == 0


def test_identify_search_rate_limit_propagates():
    class _RateLimited:
        def search(self, query, media_type):
            del query, media_type
            raise TmdbError("RATE_LIMIT", "slow down")

    with pytest.raises(TmdbError):
        identify_search(_RateLimited(), "Exemple", 2020, "film", with_glance=False)


def test_append_to_response_parsing_release_dates_and_providers():
    row = {
        "genres": [{"name": "Exemple genre"}],
        "vote_average": 7.5,
        "vote_count": 100,
        "runtime": 120,
        "original_language": "fr",
        "belongs_to_collection": {"id": 9, "name": "Exemple saga"},
        "watch/providers": {
            "results": {
                "FR": {"flatrate": [{"provider_name": "Exemple stream"}]},
            }
        },
        "release_dates": {
            "results": [
                {
                    "iso_3166_1": "FR",
                    "release_dates": [{"type": 3, "release_date": "2020-01-01T00:00:00.000Z"}],
                }
            ]
        },
        "external_ids": {"imdb_id": "tt0000001"},
    }
    extra = detail_enrichment_from_row(row, media_type="film", release_dates=row["release_dates"])
    assert extra["genres"] == ("Exemple genre",)
    assert extra["imdb_id"] == "tt0000001"
    assert providers_fr_from_row(row) == ["Exemple stream"]
    assert extra["release_by_region"]["FR"]["theatrical"] == "2020-01-01"


def test_retry_identify_respects_daily_attempt_and_rotation(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    rows = []
    for idx in range(4):
        rows.append(
            Media(
                type="film",
                title=f"Exemple-{idx}",
                year=2020 + idx,
                followed=False,
                tmdb_identify_status="pending",
            )
        )
    db.add_all(rows)
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    class _NoMatch(_SearchOnly):
        def __init__(self) -> None:
            super().__init__([])

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _NoMatch())

    from sqlalchemy import select

    first = maybe_retry_pending_identify(db, settings, now=NOW)
    assert first == 0
    attempted = list(
        db.scalars(select(Media.id).where(Media.tmdb_identify_last_attempt_at.is_not(None))).all()
    )
    assert len(attempted) <= 3
    cursor = db.get(Setting, _IDENTIFY_CURSOR)
    assert cursor is not None
    second = maybe_retry_pending_identify(db, settings, now=NOW + timedelta(hours=1))
    assert second == 0
    db.close()


def test_retry_identify_backoff_on_rate_limit(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    db.add(
        Media(
            type="film",
            title="Exemple-rate",
            year=2020,
            followed=False,
            tmdb_identify_status="pending",
        )
    )
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    class _Limited(_SearchOnly):
        def __init__(self) -> None:
            super().__init__([TmdbHit(1, "film", "Exemple-rate", 2020, None, None)])

        def search(self, query, media_type):
            raise TmdbError("RATE_LIMIT", "quota")

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _Limited())
    maybe_retry_pending_identify(db, settings, now=NOW)
    backoff = db.get(Setting, _IDENTIFY_BACKOFF_KEY)
    assert backoff is not None and backoff.value
    db.close()


def test_unfollowed_enrichment_refresh(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    media = Media(type="film", title="Exemple-nf", year=2020, followed=False, tmdb_id=42)
    db.add(media)
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    class _Fetch:
        def fetch(self, tmdb_id: int, media_type: str):
            return TmdbTitle(
                tmdb_id,
                media_type,
                "Exemple-nf",
                2020,
                None,
                None,
                None,
                None,
                (),
                vote_average=8.0,
                vote_count=10,
                genres=("Exemple genre",),
            )

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _Fetch())
    count = maybe_refresh_unfollowed_enrichment(db, settings, now=NOW)
    assert count == 1
    row = db.get(Media, media.id)
    assert row.tmdb_vote_average == 8.0
    db.close()


def test_media_out_exposes_enrichment_fields(client, app, monkeypatch):
    login(client)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Exemple-api", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201
    mid = created.json()["id"]
    db = app.state.session_factory()
    row = db.get(Media, mid)
    row.tmdb_vote_average = 7.2
    row.tmdb_genres = ["Exemple genre"]
    row.tmdb_watch_providers_fr = ["Exemple stream"]
    row.tmdb_release_by_region = {"FR": {"theatrical": "2020-01-01", "digital": None, "physical": None}}
    db.commit()
    db.close()
    detail = client.get(f"/api/v1/media/{mid}").json()
    assert detail["tmdb_vote_average"] == 7.2
    assert detail["tmdb_genres"] == ["Exemple genre"]
    assert detail["tmdb_watch_providers_fr"] == ["Exemple stream"]
    assert detail["tmdb_release_by_region"]["FR"]["theatrical"] == "2020-01-01"


def test_followed_film_enrichment_on_refresh(client, app, monkeypatch):
    """B4 / M12: followed films get enrichment via apply_new_seasons."""
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    media = Media(
        type="film",
        title="Exemple-film-suivi",
        year=2020,
        followed=True,
        on_hold=False,
        tmdb_id=55,
    )
    db.add(media)
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    class _Fetch:
        def fetch(self, tmdb_id: int, media_type: str):
            return TmdbTitle(
                tmdb_id,
                media_type,
                "Exemple-film-suivi",
                2020,
                None,
                None,
                None,
                None,
                (),
                vote_average=6.5,
                vote_count=20,
                genres=("Exemple genre",),
            )

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _Fetch())
    monkeypatch.setattr("mediatracker.core.seasons._next_unfollowed", lambda db, now: None)
    count = maybe_refresh_films(db, settings, now=NOW)
    assert count == 1
    row = db.get(Media, media.id)
    assert row.tmdb_vote_average == 6.5
    assert row.tmdb_genres == ["Exemple genre"]
    db.close()

"""Mutation-killing tests for TMDB service (B6 / revue r2)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from mediatracker.core.library_plan import identify_folder
from mediatracker.core.seasons import (
    _IDENTIFY_ATTEMPT_EVERY,
    _IDENTIFY_BACKOFF_KEY,
    apply_new_seasons,
    maybe_refresh_films,
    maybe_refresh_seasons,
    maybe_retry_pending_identify,
)
from mediatracker.core.tmdb.apply import apply_tmdb, owner_for_tmdb
from mediatracker.db.models import Media, Setting
from mediatracker.integrations.jellyfin_history_import import HistoryLine, apply_import
from mediatracker.integrations.tmdb import TmdbError, TmdbHit, TmdbTitle

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


class _SearchOnly:
    def __init__(self, hits: list[TmdbHit]) -> None:
        self.hits = hits

    def search(self, query: str, media_type: str):
        del query, media_type
        return self.hits


def test_m3b_worker_invokes_retry_identify(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    calls: list[int] = []

    def _retry(*_args, **_kwargs):
        calls.append(1)
        return 0

    monkeypatch.setattr("mediatracker.core.seasons._next_series", lambda db, now: None)
    monkeypatch.setattr("mediatracker.core.seasons.maybe_retry_pending_identify", _retry)
    monkeypatch.setattr("mediatracker.core.seasons.maybe_refresh_films", lambda *a, **k: 0)
    maybe_refresh_seasons(db, settings, now=NOW)
    db.close()
    assert calls == [1]


def test_m7_library_plan_does_not_pick_first_ambiguous_hit(tmp_path, monkeypatch):
    folder = tmp_path / "Exemple Dossier"
    folder.mkdir()

    class _Tmdb:
        def search(self, query, media_type):
            return [
                TmdbHit(1, media_type, "Exemple A", 2020, None, None),
                TmdbHit(2, media_type, "Exemple B", 2021, None, None),
            ]

    tmdb_id, conf, _source, suggestions = identify_folder(
        folder, media_type="film", tmdb=_Tmdb()
    )
    assert tmdb_id is None
    assert conf == "ambiguous"
    assert len(suggestions) == 2


def test_m10_unfollowed_refresh_runs_after_films(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    unfollowed_calls: list[int] = []

    monkeypatch.setattr("mediatracker.core.seasons._next_film", lambda db, now: None)
    monkeypatch.setattr(
        "mediatracker.core.seasons.maybe_refresh_unfollowed_enrichment",
        lambda *a, **k: unfollowed_calls.append(1) or 0,
    )
    maybe_refresh_films(db, settings, now=NOW)
    db.close()
    assert unfollowed_calls == [1]


def test_m11_apply_tmdb_writes_enrichment(client, app):
    del client
    db = app.state.session_factory()
    media = Media(type="film", title="Exemple", year=2020, followed=True, tmdb_id=1)
    db.add(media)
    db.commit()
    fetched = TmdbTitle(
        1,
        "film",
        "Exemple",
        2020,
        None,
        None,
        None,
        None,
        (),
        vote_average=9.1,
        vote_count=3,
        genres=("Exemple genre",),
    )
    apply_tmdb(db, media, fetched, imdb_id=None, moment=NOW)
    assert media.tmdb_vote_average == 9.1
    assert media.tmdb_genres == ["Exemple genre"]
    db.close()


def test_b1a_identify_retry_once_per_day(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    row = Media(type="film", title="Exemple-jour", year=2020, followed=False, tmdb_identify_status="pending")
    db.add(row)
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _SearchOnly([]))
    maybe_retry_pending_identify(db, settings, now=NOW)
    db.refresh(row)
    first = row.tmdb_identify_last_attempt_at
    assert first is not None
    maybe_retry_pending_identify(db, settings, now=NOW + timedelta(hours=2))
    db.refresh(row)
    assert row.tmdb_identify_last_attempt_at == first
    db.close()


def test_b1b_identify_cursor_rotates(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    for idx in range(4):
        db.add(
            Media(
                type="film",
                title=f"Exemple-{idx}",
                year=2020,
                followed=False,
                tmdb_identify_status="pending",
            )
        )
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _SearchOnly([]))
    maybe_retry_pending_identify(db, settings, now=NOW)
    from sqlalchemy import func, select

    count1 = db.scalar(
        select(func.count()).select_from(Media).where(Media.tmdb_identify_last_attempt_at.is_not(None))
    )
    maybe_retry_pending_identify(
        db,
        settings,
        now=NOW + _IDENTIFY_ATTEMPT_EVERY + timedelta(minutes=1),
    )
    count2 = db.scalar(
        select(func.count()).select_from(Media).where(Media.tmdb_identify_last_attempt_at.is_not(None))
    )
    assert count2 > count1
    db.close()


def test_b1c2_identify_backoff_skips_next_pass(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    db.add(Media(type="film", title="Exemple-bo-a", year=2020, followed=False, tmdb_identify_status="pending"))
    db.add(Media(type="film", title="Exemple-bo-b", year=2020, followed=False, tmdb_identify_status="pending"))
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    class _Limited(_SearchOnly):
        def search(self, query, media_type):
            raise TmdbError("RATE_LIMIT", "slow")

    searches = 0
    client_mock = _Limited([])

    def _open(db, s):
        nonlocal searches
        searches += 1
        return client_mock

    monkeypatch.setattr("mediatracker.core.seasons._open", _open)
    maybe_retry_pending_identify(db, settings, now=NOW)
    assert searches == 1
    assert db.get(Setting, _IDENTIFY_BACKOFF_KEY) is not None
    searches = 0
    maybe_retry_pending_identify(db, settings, now=NOW + timedelta(minutes=1))
    assert searches == 0
    db.close()


def test_b1d_identify_skips_duplicate_tmdb(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    title = "Exemple-doublon"
    db.add(Media(type="film", title=title, year=2020, followed=False, tmdb_id=42))
    pending = Media(
        type="film",
        title=title,
        year=2020,
        followed=False,
        tmdb_identify_status="pending",
    )
    db.add(pending)
    db.commit()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")

    class _Hit(_SearchOnly):
        def __init__(self):
            super().__init__([TmdbHit(42, "film", title, 2020, None, None)])

        def fetch(self, tmdb_id, media_type):
            return TmdbTitle(tmdb_id, media_type, title, 2020, None, None, None, None, None, ())

    monkeypatch.setattr("mediatracker.core.seasons._open", lambda db, s: _Hit())
    resolved = maybe_retry_pending_identify(db, settings, now=NOW)
    assert resolved == 0
    db.refresh(pending)
    assert pending.tmdb_id is None
    assert pending.tmdb_identify_status == "pending"
    assert owner_for_tmdb(db, 42, "film", exclude_media_id=pending.id) is not None
    db.close()


def test_m12_apply_new_seasons_enriches_series(client, app):
    del client
    db = app.state.session_factory()
    media = Media(type="series", title="Exemple-serie", year=2020, followed=True, tmdb_id=7)
    db.add(media)
    db.commit()
    fetched = TmdbTitle(
        7,
        "series",
        "Exemple-serie",
        2020,
        None,
        None,
        None,
        None,
        (),
        vote_average=8.8,
        genres=("Exemple genre",),
    )
    apply_new_seasons(db, media, fetched, NOW)
    assert media.tmdb_vote_average == 8.8
    db.close()


def test_apply_import_borrows_tmdb_client_once(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    opens: list[int] = []

    class _Client:
        def find_external(self, *a, **k):
            return None, None

        def search(self, *a, **k):
            return []

        def fetch(self, *a, **k):
            return None

        def close(self):
            return None

    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (opens.append(1) or _Client(), True),
    )
    lines = [
        HistoryLine(1, "movie", "Film-a", 2020, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
        HistoryLine(2, "movie", "Film-b", 2021, None, None, None, None, None, None, None, NOW, 1, "jellyfin"),
    ]
    apply_import(db, settings, settings.secret_key, lines, now=NOW)
    db.close()
    assert len(opens) == 1

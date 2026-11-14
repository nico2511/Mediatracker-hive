"""Unit tests for mediatracker.core.tmdb helpers."""

from dataclasses import dataclass

from mediatracker.core.tmdb import (
    ensure_imdb,
    fetch_title,
    open_client,
)
from mediatracker.integrations.tmdb import TmdbHit


@dataclass
class _Media:
    imdb_id: str | None = None
    tmdb_id: int | None = None
    type: str = "series"


class _Client:
    def __init__(self, imdb: str | None = "tt0000001") -> None:
        self.imdb = imdb
        self.glanced = False

    def external_imdb(self, tmdb_id: int, media_type: str) -> str | None:
        del tmdb_id, media_type
        return self.imdb

    def fetch(self, tmdb_id: int, media_type: str):
        from mediatracker.integrations.tmdb import TmdbTitle

        del tmdb_id, media_type
        return TmdbTitle(
            tmdb_id=7,
            media_type="series",
            title="Exemple série",
            year=2020,
            overview=None,
            poster_url=None,
            series_status="ended",
            release_date=None,
            seasons=(),
            imdb_id=self.imdb,
        )

    def glance(self, tmdb_id: int, media_type: str):
        del tmdb_id, media_type
        self.glanced = True
        return TmdbHit(
            tmdb_id=42,
            media_type="series",
            title="Exemple A",
            year=2024,
            overview="Résumé court.",
            poster_url="https://image.tmdb.org/t/p/w500/x.jpg",
        )


def test_open_client_without_token(client):
    app = client.app
    db = app.state.session_factory()
    try:
        client, owned = open_client(db, app.state.settings)
    finally:
        db.close()
    assert client is None
    assert owned is False


def test_ensure_imdb_keeps_existing_id():
    media = _Media(imdb_id="tt1234567")
    assert ensure_imdb(None, media, None) == "tt1234567"


def test_ensure_imdb_fills_from_tmdb(client, monkeypatch):
    app = client.app
    media = _Media(tmdb_id=7)
    tmdb_client = _Client()
    settings = app.state.settings
    db = app.state.session_factory()

    monkeypatch.setattr(
        "mediatracker.core.tmdb.token.read_tmdb_token",
        lambda db, key: "token",
    )
    monkeypatch.setattr(
        "mediatracker.core.tmdb.fetch.shared_tmdb",
        lambda token: tmdb_client,
    )

    try:
        assert ensure_imdb(db, media, settings) == "tt0000001"
    finally:
        db.close()
    assert media.imdb_id == "tt0000001"


def test_fetch_title_returns_none_without_token(client, monkeypatch):
    app = client.app
    settings = app.state.settings
    db = app.state.session_factory()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    try:
        assert fetch_title(db, settings, 1, "film") is None
    finally:
        db.close()


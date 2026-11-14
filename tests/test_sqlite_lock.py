"""SQLite stays writable under the worker, and a lock is not a 500."""

import sqlite3
from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from tests.conftest import csrf, login

from mediatracker.db.models import ApiToken
from mediatracker.db.session import SQLITE_BUSY_MS
from mediatracker.integrations.tmdb import TmdbError


def test_sqlite_enables_wal_and_waits(app):
    db = app.state.session_factory()
    try:
        assert db.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert db.execute(text("PRAGMA busy_timeout")).scalar() == SQLITE_BUSY_MS
        assert db.execute(text("PRAGMA synchronous")).scalar() == 1
    finally:
        db.close()


def test_locked_write_is_a_french_503(client, monkeypatch):
    login(client)

    def locked(self, *args, **kwargs):
        del self, args, kwargs
        raise OperationalError("INSERT INTO media", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(Session, "commit", locked)
    response = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Sorcières Démo 2"},
        headers=csrf(client),
    )
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["title"] == "Base occupée"
    assert "Réessayez" in body["detail"]
    assert response.headers["retry-after"] == "5"


def test_missing_tmdb_fiche_stays_a_404(client, monkeypatch):
    """The 404 in front of the locked 500 is TMDB, not a missing route."""
    login(client)
    saved = client.patch(
        "/api/v1/settings",
        json={"values": {"tmdb_read_token": "tmdb-test-token"}},
        headers=csrf(client),
    )
    assert saved.status_code == 200, saved.text

    class Missing:
        def __init__(self, token: str, **_kwargs) -> None:
            del token

        def fetch(self, tmdb_id: int, media_type: str):
            del tmdb_id, media_type
            raise TmdbError("NOT_FOUND", "Cette fiche TMDB est introuvable.")

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.integrations.tmdb.TmdbClient", Missing)
    response = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Sorcières Démo 2", "tmdb_id": 1},
        headers=csrf(client),
    )
    assert response.status_code == 404, response.text
    assert "introuvable" in response.json()["detail"]


def test_bearer_last_used_is_throttled_and_a_lock_is_ignored(client, app, monkeypatch):
    login(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": "ha", "scopes": ["read"]},
        headers=csrf(client),
    )
    assert created.status_code == 201, created.text
    headers = {"Authorization": f"Bearer {created.json()['token']}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200

    db = app.state.session_factory()
    try:
        row = db.query(ApiToken).one()
        first = row.last_used_at
        assert first is not None
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
        db.refresh(row)
        assert row.last_used_at == first

        row.last_used_at = first - timedelta(minutes=6)
        db.commit()
        db.refresh(row)
        aged = row.last_used_at

        def locked(self, *args, **kwargs):
            del self, args, kwargs
            raise OperationalError(
                "UPDATE api_token",
                {},
                sqlite3.OperationalError("database is locked"),
            )

        monkeypatch.setattr(Session, "commit", locked)
        response = client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200, response.text
        db.rollback()
        db.refresh(row)
        assert row.last_used_at == aged
    finally:
        db.close()

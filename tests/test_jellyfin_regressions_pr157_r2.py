"""Import Jellyfin : idempotence et option probables (post-refonte moteur)."""

from __future__ import annotations

from datetime import datetime, timezone

from mediatracker.integrations.jellyfin_db_export import export_history_bundle
from mediatracker.integrations.jellyfin_history_import import apply_import, history_lines_from_watch_rows
from mediatracker.integrations.tmdb import TmdbTitle

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def test_second_db_export_import_creates_zero(client, app, monkeypatch, tmp_path):
    del client
    import sqlite3

    db_path = tmp_path / "j.db"
    user = "11111111-1111-1111-1111-111111111111"
    movie = "22222222-2222-2222-2222-222222222222"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        f"""
        CREATE TABLE Users (Id TEXT PRIMARY KEY, Username TEXT NOT NULL);
        INSERT INTO Users VALUES ('{user}', 'demo');
        CREATE TABLE BaseItems (
            Id TEXT PRIMARY KEY, Type TEXT, Name TEXT, ProductionYear INTEGER,
            SeriesName TEXT, ParentIndexNumber INTEGER, IndexNumber INTEGER,
            SeriesId TEXT, IsMovie INTEGER, RunTimeTicks INTEGER
        );
        INSERT INTO BaseItems VALUES ('{movie}', 'Movie', 'Film Démo', 2020, NULL, NULL, NULL, NULL, 1, NULL);
        CREATE TABLE BaseItemProviders (
            ItemId TEXT, ProviderId TEXT, ProviderValue TEXT, PRIMARY KEY (ItemId, ProviderId)
        );
        INSERT INTO BaseItemProviders VALUES ('{movie}', 'Tmdb', '900001');
        CREATE TABLE UserData (
            ItemId TEXT, UserId TEXT, CustomDataKey TEXT, Played INTEGER, PlayCount INTEGER,
            LastPlayedDate TEXT, PRIMARY KEY (ItemId, UserId, CustomDataKey)
        );
        INSERT INTO UserData VALUES ('{movie}', '{user}', '{movie.lower()}', 1, 1, '2024-06-01T12:00:00.0000000Z');
        CREATE TABLE ActivityLogs (
            Id INTEGER PRIMARY KEY AUTOINCREMENT, Type TEXT, Name TEXT, UserId TEXT,
            ItemId TEXT, DateCreated TEXT
        );
        """
    )
    conn.commit()
    conn.close()

    def fake_maybe_fetch(
        db, settings, secret_key, tmdb_id, media_type, *, tmdb_cache=None, import_tx=None, **kwargs
    ):
        del import_tx
        del db, settings, secret_key
        return TmdbTitle(tmdb_id, media_type, "Film Démo", 2020, None, None, None, None, ())

    monkeypatch.setattr("mediatracker.integrations.jellyfin_history_import._maybe_fetch", fake_maybe_fetch)

    bundle = export_history_bundle(jellyfin_db=db_path, user_id=user)
    lines = history_lines_from_watch_rows(bundle.rows)
    settings = app.state.settings
    db = app.state.session_factory()
    try:
        first = apply_import(db, settings, settings.secret_key, lines)
        db.commit()
        assert first.created_media >= 0
        second = apply_import(db, settings, settings.secret_key, lines)
        assert second.created_media == 0
        assert second.applied == 0
    finally:
        db.close()

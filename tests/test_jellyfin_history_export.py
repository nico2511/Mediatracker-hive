"""Tests for tools/jellyfin_history_export.py."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

from mediatracker.integrations.jellyfin_db_export import WatchRow, export_history, merge_rows, replace, write_csv

USER_ID = "11111111-1111-1111-1111-111111111111"
MOVIE_ID = "22222222-2222-2222-2222-222222222222"
EP_ID = "33333333-3333-3333-3333-333333333333"
SERIES_ID = "44444444-4444-4444-4444-444444444444"
TMDB_FILM = 990001
TMDB_SERIES = 990002


def _create_efcore_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE Users (Id TEXT PRIMARY KEY, Username TEXT NOT NULL);
        INSERT INTO Users VALUES ('{USER_ID}', 'demo');
        CREATE TABLE BaseItems (
            Id TEXT PRIMARY KEY, Type TEXT, Name TEXT, ProductionYear INTEGER,
            SeriesName TEXT, ParentIndexNumber INTEGER, IndexNumber INTEGER,
            SeriesId TEXT, IsMovie INTEGER
        );
        INSERT INTO BaseItems VALUES ('{MOVIE_ID}', 'Movie', 'Film Démo', 2020, NULL, NULL, NULL, NULL, 1);
        INSERT INTO BaseItems VALUES ('{SERIES_ID}', 'Series', 'Série Démo', 2021, NULL, NULL, NULL, NULL, 0);
        INSERT INTO BaseItems VALUES ('{EP_ID}', 'Episode', 'Épisode démo', 2021, 'Série Démo', 1, 2, '{SERIES_ID}', 0);
        CREATE TABLE BaseItemProviders (ItemId TEXT, ProviderId TEXT, ProviderValue TEXT,
            PRIMARY KEY (ItemId, ProviderId));
        INSERT INTO BaseItemProviders VALUES ('{MOVIE_ID}', 'Tmdb', '{TMDB_FILM}');
        INSERT INTO BaseItemProviders VALUES ('{SERIES_ID}', 'Tmdb', '{TMDB_SERIES}');
        CREATE TABLE UserData (
            ItemId TEXT, UserId TEXT, CustomDataKey TEXT, Played INTEGER, PlayCount INTEGER,
            LastPlayedDate TEXT, PRIMARY KEY (ItemId, UserId, CustomDataKey)
        );
        INSERT INTO UserData VALUES ('{MOVIE_ID}', '{USER_ID}', '{MOVIE_ID.lower()}', 1, 2, '2024-06-01T12:00:00Z');
        INSERT INTO UserData VALUES ('{EP_ID}', '{USER_ID}', '{EP_ID.lower()}', 1, 1, '2024-07-01T10:00:00Z');
        """
    )
    conn.commit()
    conn.close()


def test_export_userdata_only(tmp_path):
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    rows = export_history(jf, user="demo")
    assert len(rows) == 2
    movie_hex = MOVIE_ID.replace("-", "").lower()
    EP_ID.replace("-", "").lower()
    by_id = {row.jellyfin_item_id: row for row in rows}
    assert by_id[movie_hex].type == "movie"
    assert by_id[movie_hex].title == "Film Démo"
    assert by_id[movie_hex].play_count >= 1


def test_merge_keeps_latest_date():
    older = WatchRow(
        type="movie",
        title="Film Démo",
        year=2020,
        series_title=None,
        season=None,
        episode=None,
        tmdb_id=1,
        imdb_id=None,
        tvdb_id=None,
        jellyfin_item_id="a",
        watched_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        play_count=1,
        source="userdata",
    )
    newer = replace(
        older,
        watched_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
        play_count=3,
        source="activity_log",
    )
    merged = merge_rows([older, newer])
    assert len(merged) == 1
    assert merged[0].watched_at == datetime(2024, 2, 1, tzinfo=timezone.utc)
    assert merged[0].play_count == 3


def test_write_csv_columns(tmp_path):
    out = tmp_path / "out.csv"
    row = WatchRow(
        type="movie",
        title="Film Démo",
        year=2020,
        series_title=None,
        season=None,
        episode=None,
        tmdb_id=TMDB_FILM,
        imdb_id=None,
        tvdb_id=None,
        jellyfin_item_id=MOVIE_ID.replace("-", "").lower(),
        watched_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
        play_count=1,
        source="userdata",
    )
    write_csv([row], out)
    text = out.read_text(encoding="utf-8")
    reader = csv.DictReader(StringIO(text))
    assert reader.fieldnames is not None
    assert "date_vue" in reader.fieldnames
    data = next(reader)
    assert data["titre_film"] == "Film Démo"
    assert data["type"] == "film"

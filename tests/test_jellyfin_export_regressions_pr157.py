"""Régressions export Jellyfin encore pertinentes après refonte moteur."""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from mediatracker.integrations.jellyfin_db_export import export_history_bundle, rows_to_csv_text
from mediatracker.integrations.jellyfin_efcore_data import PLACEHOLDER_ITEM_ID, normalize_jellyfin_id
from mediatracker.integrations.jellyfin_history_import import history_lines_from_watch_rows

USER = "11111111-1111-1111-1111-111111111111"
PH = PLACEHOLDER_ITEM_ID


def _efcore_db(path: Path, *, userdata: str = "", items: str = "") -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE Users (Id TEXT PRIMARY KEY, Username TEXT NOT NULL);
        INSERT INTO Users VALUES ('{USER}', 'demo');
        CREATE TABLE BaseItems (
            Id TEXT PRIMARY KEY, Type TEXT, Name TEXT, ProductionYear INTEGER,
            SeriesName TEXT, ParentIndexNumber INTEGER, IndexNumber INTEGER,
            SeriesId TEXT, IsMovie INTEGER, RunTimeTicks INTEGER
        );
        {items}
        CREATE TABLE BaseItemProviders (ItemId TEXT, ProviderId TEXT, ProviderValue TEXT,
            PRIMARY KEY (ItemId, ProviderId));
        CREATE TABLE UserData (
            ItemId TEXT, UserId TEXT, CustomDataKey TEXT, Played INTEGER, PlayCount INTEGER,
            LastPlayedDate TEXT, PlaybackPositionTicks INTEGER, RetentionDate TEXT,
            PRIMARY KEY (ItemId, UserId, CustomDataKey)
        );
        {userdata}
        CREATE TABLE ActivityLogs (
            Id INTEGER PRIMARY KEY AUTOINCREMENT, Type TEXT, Name TEXT, UserId TEXT, ItemId TEXT, DateCreated TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def test_no_placeholder_item_id_in_export(tmp_path):
    db = tmp_path / "j.db"
    guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    ts = "2024-01-01T12:00:00.0000000Z"
    _efcore_db(
        db,
        userdata=f"""
        INSERT INTO UserData (
            ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate
        ) VALUES ('{PH}', '{USER}', '{guid}', 1, 1, '{ts}');
        INSERT INTO UserData (
            ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate
        ) VALUES ('{PH}', '{USER}', '990001001002', 1, 1, '{ts}');
        """,
    )
    bundle = export_history_bundle(jellyfin_db=db, user_id=USER)
    assert bundle.rows
    for row in bundle.rows:
        if row.jellyfin_item_id:
            assert normalize_jellyfin_id(row.jellyfin_item_id)
        for part in (row.jellyfin_ids or "").split():
            normalized = normalize_jellyfin_id(part)
            if normalized:
                assert normalized != normalize_jellyfin_id(PH) or normalize_jellyfin_id(PH) == ""
    lines = history_lines_from_watch_rows(bundle.rows)
    assert all(not line.jellyfin_item_id or normalize_jellyfin_id(line.jellyfin_item_id) for line in lines)


def test_sqlite_and_zip_csv_identical(tmp_path):
    db = tmp_path / "j.db"
    movie = "22222222-2222-2222-2222-222222222222"
    _efcore_db(
        db,
        items=f"INSERT INTO BaseItems VALUES ('{movie}', 'Movie', 'Film', 2020, NULL, NULL, NULL, NULL, 1, NULL);",
        userdata=(
            "INSERT INTO UserData (ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate) "
            f"VALUES ('{movie}', '{USER}', '{movie.lower()}', 1, 1, '2024-06-01T12:00:00.0000000Z');"
        ),
    )
    zip_path = tmp_path / "backup.zip"
    tables = {
        "Users": [{"Id": USER, "Username": "demo"}],
        "BaseItems": [
            {
                "Id": movie.lower(),
                "Type": "Movie",
                "Name": "Film",
                "ProductionYear": 2020,
                "SeriesName": None,
                "ParentIndexNumber": None,
                "IndexNumber": None,
                "SeriesId": None,
                "IsMovie": True,
            }
        ],
        "BaseItemProviders": [],
        "UserData": [
            {
                "ItemId": movie.lower(),
                "UserId": USER,
                "CustomDataKey": movie.lower(),
                "Played": True,
                "PlayCount": 1,
                "LastPlayedDate": "2024-06-01T12:00:00.0000000Z",
            }
        ],
        "ActivityLogs": [],
    }
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name, payload in tables.items():
            zf.writestr(f"Database/{name}.json", json.dumps(payload))
    sqlite_csv = rows_to_csv_text(csv_rows=export_history_bundle(jellyfin_db=db, user_id=USER).csv_rows)
    zip_csv = rows_to_csv_text(csv_rows=export_history_bundle(backup_zip=zip_path, user_id=USER).csv_rows)
    assert sqlite_csv == zip_csv

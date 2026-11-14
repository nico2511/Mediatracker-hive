"""API tests for guided Jellyfin DB import sessions."""

from __future__ import annotations

import sqlite3
from io import BytesIO
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.integrations.jellyfin_db_sessions import sessions_root

USER_ID = "11111111-1111-1111-1111-111111111111"
USER_ID_B = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
MOVIE_ID = "22222222-2222-2222-2222-222222222222"
EP_ID = "33333333-3333-3333-3333-333333333333"
SERIES_ID = "44444444-4444-4444-4444-444444444444"
DELETED_ID = "55555555-5555-5555-5555-555555555555"
TMDB_FILM = 990001
TMDB_SERIES = 990002


def _create_efcore_db(path: Path, *, extra_user: bool = False) -> None:
    conn = sqlite3.connect(path)
    script = f"""
        CREATE TABLE Users (Id TEXT PRIMARY KEY, Username TEXT NOT NULL);
        INSERT INTO Users VALUES ('{USER_ID}', 'compte_demo');
    """
    if extra_user:
        script += f"INSERT INTO Users VALUES ('{USER_ID_B}', 'compte_b');\n"
    script += f"""
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
        INSERT INTO BaseItemProviders VALUES ('{SERIES_ID}', 'Tvdb', '990099');
        CREATE TABLE UserData (
            ItemId TEXT, UserId TEXT, CustomDataKey TEXT, Played INTEGER, PlayCount INTEGER,
            LastPlayedDate TEXT, PRIMARY KEY (ItemId, UserId, CustomDataKey)
        );
        INSERT INTO UserData VALUES ('{MOVIE_ID}', '{USER_ID}', '{MOVIE_ID.lower()}', 1, 2, '2024-06-01T12:00:00Z');
        INSERT INTO UserData VALUES ('{EP_ID}', '{USER_ID}', '{EP_ID.lower()}', 1, 1, '2024-07-01T10:00:00Z');
    """
    conn.executescript(script)
    conn.commit()
    conn.close()


def _create_playback_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE PlaybackActivity (
            DateCreated DATETIME NOT NULL,
            UserId TEXT,
            ItemId TEXT,
            ItemType TEXT,
            ItemName TEXT,
            PlaybackMethod TEXT,
            ClientName TEXT,
            DeviceName TEXT,
            PlayDuration INT
        );
        INSERT INTO PlaybackActivity VALUES (
            '2024-08-01T18:00:00Z', '{USER_ID}', '{DELETED_ID}', 'Episode',
            'Épisode supprimé', 'DirectPlay', 'Web', 'Navigateur', 3600
        );
        """
    )
    conn.commit()
    conn.close()


def _auth(client):
    login(client)
    return csrf(client)


def _upload_session(client, app, *, jellyfin: Path | None = None, backup_zip: Path | None = None):
    headers = _auth(client)
    files = []
    if jellyfin is not None:
        files.append(("jellyfin_db", ("jellyfin.db", jellyfin.read_bytes(), "application/octet-stream")))
    if backup_zip is not None:
        files.append(("backup_zip", ("backup.zip", backup_zip.read_bytes(), "application/zip")))
    response = client.post("/api/v1/library/jellyfin-history/db/session", headers=headers, files=files)
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    root = sessions_root(app.state.settings.config_dir) / session_id
    assert root.is_dir()
    return session_id, root


def test_db_preview_same_as_csv_from_export(client, app, tmp_path):
    from mediatracker.integrations.jellyfin_db_export import (
        export_history_for_user_id_with_stats,
        rows_to_reimport_csv_text,
        watch_row_to_reimport,
    )

    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    session_id, _ = _upload_session(client, app, jellyfin=jf)
    headers = _auth(client)
    preview_db = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/preview?sync=true",
        headers=headers,
        json={"user_id": USER_ID},
    ).json()
    bundle = export_history_for_user_id_with_stats(jellyfin_db=jf, user_id=USER_ID)
    csv_text = rows_to_reimport_csv_text([watch_row_to_reimport(row) for row in bundle.rows])
    csv_preview = client.post(
        "/api/v1/library/jellyfin-history/import?dry_run=true&sync=true",
        headers=headers,
        files={"upload": ("hist.csv", BytesIO(csv_text.encode()), "text/csv")},
    ).json()
    assert preview_db["line_count"] == csv_preview["line_count"]
    assert preview_db["matched_count"] == csv_preview["matched_count"]
    assert preview_db["create_count"] == csv_preview["create_count"]
    assert preview_db["unmatched_count"] == csv_preview["unmatched_count"]


def test_db_session_lists_users_and_preview(client, app, tmp_path):
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf, extra_user=True)
    session_id, _root = _upload_session(client, app, jellyfin=jf)
    headers = _auth(client)
    users = client.get(f"/api/v1/library/jellyfin-history/db/session/{session_id}/users", headers=headers)
    assert users.status_code == 200
    body = users.json()["users"]
    assert len(body) == 2
    assert body[0]["username"] == "compte_demo"

    preview_db = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/preview?sync=true",
        headers=headers,
        json={"user_id": USER_ID},
    )
    assert preview_db.status_code == 200
    body = preview_db.json()
    assert body["line_count"] == 2
    assert body["export_sure_movies"] + body["export_sure_episodes"] == 2


def test_db_session_csv_download(client, app, tmp_path):
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    session_id, _ = _upload_session(client, app, jellyfin=jf)
    headers = _auth(client)
    response = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/export.csv",
        headers=headers,
        json={"user_id": USER_ID},
    )
    assert response.status_code == 200
    assert "Film Démo" in response.text
    assert response.headers["content-type"].startswith("text/csv")


def test_db_session_removed_after_confirm_and_abandon(client, app, tmp_path):
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    session_id, root = _upload_session(client, app, jellyfin=jf)
    headers = _auth(client)
    confirm = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/import?dry_run=false&sync=true",
        headers=headers,
        json={"user_id": USER_ID},
    )
    assert confirm.status_code == 200
    assert not root.exists()

    session_id2, root2 = _upload_session(client, app, jellyfin=jf)
    abandon = client.delete(
        f"/api/v1/library/jellyfin-history/db/session/{session_id2}",
        headers=csrf(client),
    )
    assert abandon.status_code == 204
    assert not root2.exists()


def test_db_session_rejects_non_sqlite(client, app, tmp_path):
    junk = tmp_path / "jellyfin.db"
    junk.write_text("not a database", encoding="utf-8")
    headers = _auth(client)
    response = client.post(
        "/api/v1/library/jellyfin-history/db/session",
        headers=headers,
        files=[("jellyfin_db", ("jellyfin.db", junk.read_bytes(), "application/octet-stream"))],
    )
    assert response.status_code == 422
    root = sessions_root(app.state.settings.config_dir)
    if root.is_dir():
        assert not any(root.iterdir())


def test_db_session_rejects_oversize(client, app, tmp_path, monkeypatch):
    jf = tmp_path / "jellyfin.db"
    _create_efcore_db(jf)
    monkeypatch.setattr(app.state.settings, "jellyfin_history_upload_max_bytes", 100)
    headers = _auth(client)
    response = client.post(
        "/api/v1/library/jellyfin-history/db/session",
        headers=headers,
        files=[("jellyfin_db", ("jellyfin.db", jf.read_bytes(), "application/octet-stream"))],
    )
    assert response.status_code == 413


def test_db_session_upload_error_cleans_temp(client, app):
    headers = _auth(client)
    response = client.post(
        "/api/v1/library/jellyfin-history/db/session",
        headers=headers,
        files=[("jellyfin_db", ("jellyfin.db", b"", "application/octet-stream"))],
    )
    assert response.status_code == 422
    root = sessions_root(app.state.settings.config_dir)
    if root.is_dir():
        assert not any(root.iterdir())

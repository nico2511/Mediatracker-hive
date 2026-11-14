"""Backups must not embed plaintext secrets or the app encryption key."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tests.conftest import csrf, login

from mediatracker.maintenance.backup import backup_sqlite, sqlite_path
from mediatracker.security.crypto import decrypt_secret

_DEMO_TMDB = "tmdb-demo-val"
_DEMO_JF_KEY = "jf-api-demo"


def test_backup_sqlite_hides_secrets_and_key(client, settings):
    login(client)
    patched = client.patch(
        "/api/v1/settings",
        json={"values": {"tmdb_read_token": _DEMO_TMDB, "jellyfin_api_key": _DEMO_JF_KEY}},
        headers=csrf(client),
    )
    assert patched.status_code == 200, patched.text

    source = sqlite_path(settings.database_url)
    assert source is not None and source.is_file()
    dest = Path(settings.config_dir) / "backups"
    moment = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
    backup = backup_sqlite(source, dest, keep=3, now=moment)
    assert backup is not None and backup.is_file()

    raw = backup.read_bytes()
    assert _DEMO_TMDB.encode("utf-8") not in raw
    assert _DEMO_JF_KEY.encode("utf-8") not in raw
    assert settings.secret_key.encode("utf-8") not in raw

    conn = sqlite3.connect(backup)
    try:
        tmdb_row = conn.execute(
            "select value from setting where key = ?",
            ("tmdb_read_token",),
        ).fetchone()
        jf_row = conn.execute(
            "select value from setting where key = ?",
            ("jellyfin_api_key",),
        ).fetchone()
        assert tmdb_row is not None and jf_row is not None
        assert decrypt_secret(tmdb_row[0], settings.secret_key) == _DEMO_TMDB
        assert decrypt_secret(jf_row[0], settings.secret_key) == _DEMO_JF_KEY
    finally:
        conn.close()

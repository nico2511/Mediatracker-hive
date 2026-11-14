"""Tests against anonymized Jellyfin 10.11 fixtures (README expected counts)."""

from __future__ import annotations

from pathlib import Path

import pytest

from mediatracker.integrations.jellyfin_db_export import (
    count_attached_played_without_dedup,
    export_history_bundle,
    list_users,
    rows_to_csv_text,
)
from mediatracker.integrations.jellyfin_efcore_data import open_efcore_store

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "jellyfin"
DB = FIXTURES / "jellyfin-10.11-anonyme.db"
ZIP = FIXTURES / "jellyfin-backup-anonyme.zip"
USER_ID = "C881E852-9C27-5FD8-97A2-AD79EE8FC142"
USERNAME = "utilisateur-test"

EXPECTED_ATTACHED_PLAYED_RAW = 23
EXPECTED_ATTACHED_PLAYED_DEDUP = 8
EXPECTED_KEPT_ROWS = 19
EXPECTED_SURE_MOVIES = 6
EXPECTED_SURE_EPISODES = 11


@pytest.fixture(scope="module")
def sqlite_bundle():
    assert DB.is_file(), f"Fixture manquante : {DB}"
    return export_history_bundle(jellyfin_db=DB, user_id=USER_ID)


def test_fixture_users(sqlite_bundle):
    users = list_users(DB)
    assert len(users) == 1
    assert users[0].username == USERNAME
    assert users[0].id.upper() == USER_ID


def test_attached_played_dedup_trap():
    store = open_efcore_store(jellyfin_db=DB)
    try:
        assert count_attached_played_without_dedup(store, USER_ID) == EXPECTED_ATTACHED_PLAYED_RAW
    finally:
        store.close()


def test_export_row_counts_match_readme(sqlite_bundle):
    bundle = sqlite_bundle
    assert len(bundle.rows) == EXPECTED_KEPT_ROWS
    assert bundle.stats.sure_movies == EXPECTED_SURE_MOVIES
    assert bundle.stats.sure_episodes == EXPECTED_SURE_EPISODES
    assert bundle.stats.empty_message is None


def test_backup_zip_matches_sqlite_export():
    assert ZIP.is_file()
    zip_bundle = export_history_bundle(backup_zip=ZIP, user_id=USER_ID)
    sqlite_bundle = export_history_bundle(jellyfin_db=DB, user_id=USER_ID)
    assert len(zip_bundle.rows) == len(sqlite_bundle.rows) == EXPECTED_KEPT_ROWS
    assert rows_to_csv_text(csv_rows=zip_bundle.csv_rows) == rows_to_csv_text(csv_rows=sqlite_bundle.csv_rows)


def test_activity_log_name_parsing_en_sample():
    store = open_efcore_store(jellyfin_db=DB)
    try:
        logs = store.fetch_activity_logs(USER_ID)
        en = [
            row
            for row in logs
            if row.get("Type") == "VideoPlaybackStopped" and "has finished playing" in str(row.get("Name"))
        ]
        assert len(en) == 3
    finally:
        store.close()


def test_no_inconnu_titles_in_export(sqlite_bundle):
    for row in sqlite_bundle.rows:
        assert row.title not in ("Inconnu", "Unknown")

"""Parité moteur applicatif vs reference.py (fixture 7b)."""

from __future__ import annotations

import collections
import subprocess
import sys
from pathlib import Path

import pytest

from mediatracker.integrations.jellyfin_db_export import export_history_bundle, rows_to_csv_text
from mediatracker.integrations.jellyfin_history_engine import CSV_COLUMNS, kept

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "jellyfin"
DB = FIXTURE_DIR / "jellyfin-10.11-anonyme.db"
ZIP = FIXTURE_DIR / "jellyfin-backup-anonyme.zip"
REFERENCE = Path(__file__).resolve().parent / "fixtures" / "reference.py"
USER_ID = "C881E852-9C27-5FD8-97A2-AD79EE8FC142"
USERNAME = "utilisateur-test"


def _reference_kept_rows(path: Path) -> list[dict[str, str]]:
    proc = subprocess.run(
        [sys.executable, str(REFERENCE), str(path), "--csv", "/dev/stdout"],
        check=True,
        capture_output=True,
        text=True,
    )
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(proc.stdout)))
    return [r for r in rows if r.get("utilisateur") == USERNAME and r.get("vu") in ("oui", "lecture seule")]


def _engine_kept_rows(path: Path) -> list[dict[str, str]]:
    if path.suffix == ".zip":
        bundle = export_history_bundle(backup_zip=path, user_id=USER_ID)
    else:
        bundle = export_history_bundle(jellyfin_db=path, user_id=USER_ID)
    return bundle.csv_rows


def _row_key(row: dict[str, str]) -> tuple:
    return tuple(row.get(c, "") for c in CSV_COLUMNS)


@pytest.mark.parametrize("path", [DB, ZIP])
def test_engine_csv_matches_reference(path: Path):
    assert path.is_file(), f"Fixture manquante : {path}"
    ref = sorted(_reference_kept_rows(path), key=_row_key)
    eng = sorted(_engine_kept_rows(path), key=_row_key)
    assert len(ref) == len(eng)
    a = collections.Counter(_row_key(r) for r in ref)
    b = collections.Counter(_row_key(r) for r in eng)
    assert a == b


def test_sqlite_and_zip_engine_outputs_identical():
    sqlite_bundle = export_history_bundle(jellyfin_db=DB, user_id=USER_ID)
    zip_bundle = export_history_bundle(backup_zip=ZIP, user_id=USER_ID)
    assert rows_to_csv_text(csv_rows=sqlite_bundle.csv_rows) == rows_to_csv_text(csv_rows=zip_bundle.csv_rows)


def test_readme_sure_counts():
    bundle = export_history_bundle(jellyfin_db=DB, user_id=USER_ID)
    assert bundle.stats.sure_movies == 6
    assert bundle.stats.sure_episodes == 11
    assert bundle.stats.probable_count == 0
    assert bundle.stats.non_importable_count == 2
    kept_rows = kept(bundle.csv_rows)
    assert len(kept_rows) == 19

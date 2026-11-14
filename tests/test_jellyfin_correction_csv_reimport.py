"""CSV de correction (probables / non importables) réimportable après édition."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from mediatracker.integrations.jellyfin_db_export import (
    correction_csv_rows,
    export_history_bundle,
    rows_to_reimport_csv_text,
)
from mediatracker.integrations.jellyfin_history_import import (
    REIMPORT_CSV_COLUMNS,
    apply_import,
    parse_csv_text,
    preview_import,
)
from mediatracker.integrations.tmdb import TmdbTitle

FIXTURE_DB = Path(__file__).resolve().parent / "fixtures" / "jellyfin" / "jellyfin-10.11-anonyme.db"
USER_ID = "C881E852-9C27-5FD8-97A2-AD79EE8FC142"
TMDB_FILM_FIX = 880010


@pytest.fixture(scope="module")
def correction_csv_text() -> str:
    bundle = export_history_bundle(jellyfin_db=FIXTURE_DB, user_id=USER_ID)
    rows = correction_csv_rows(bundle)
    assert len(rows) == 2
    return rows_to_reimport_csv_text(rows)


def test_correction_csv_has_reimport_columns(correction_csv_text: str):
    reader = csv.DictReader(io.StringIO(correction_csv_text))
    assert reader.fieldnames is not None
    for col in REIMPORT_CSV_COLUMNS:
        assert col in reader.fieldnames


def test_correction_csv_parses_with_ignored_info_columns(client, app, correction_csv_text: str):
    del client
    db = app.state.session_factory()
    try:
        lines = parse_csv_text(correction_csv_text)
        assert len(lines) == 1
        assert preview_import(db, lines).line_count == 1
    finally:
        db.close()


def test_incomplete_correction_line_stays_unmatched(client, app, correction_csv_text: str):
    del client
    db = app.state.session_factory()
    try:
        lines = parse_csv_text(correction_csv_text)
        result = preview_import(db, lines)
        assert len(result.unmatched) >= 1
        assert any(
            o.line.season == 2 and o.line.episode == 3 for o in result.unmatched
        )
    finally:
        db.close()


def test_correction_csv_reimport_idempotent(client, app, monkeypatch, correction_csv_text: str):
    del client
    def fake_maybe_fetch(
        db_, settings, secret_key, tmdb_id, media_type, *, tmdb_cache=None, import_tx=None, **kwargs
    ):
        del import_tx
        del db_, settings, secret_key
        key = (media_type, tmdb_id)
        if tmdb_cache is not None and key in tmdb_cache:
            return tmdb_cache[key]
        title = TmdbTitle(tmdb_id, media_type, "Film Exemple Corrigé", 2023, None, None, None, None, ())
        if tmdb_cache is not None:
            tmdb_cache[key] = title
        return title

    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_history_import._maybe_fetch",
        fake_maybe_fetch,
    )

    rows = list(csv.DictReader(io.StringIO(correction_csv_text)))
    for row in rows:
        if "Film Ancien" in (row.get("title") or ""):
            row["type"] = "movie"
            row["tmdb_id"] = str(TMDB_FILM_FIX)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(REIMPORT_CSV_COLUMNS), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    fixed_text = buffer.getvalue()

    settings = app.state.settings
    lines = parse_csv_text(fixed_text)
    db = app.state.session_factory()
    try:
        first = apply_import(db, settings, settings.secret_key, lines)
        assert first.created_media == 1
        assert first.applied == 1
        db.commit()

        second = apply_import(db, settings, settings.secret_key, lines)
        assert second.created_media == 0
        assert second.applied == 0
        assert second.already_ok == 1
    finally:
        db.close()

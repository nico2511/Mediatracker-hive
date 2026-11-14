"""Régressions import Jellyfin (revue PR #158) — sans toucher au moteur d'export."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from tests.conftest import csrf, login

from mediatracker.db.models import Media, MediaItem
from mediatracker.integrations.jellyfin_db_export import (
    ExportBundle,
    ExportStats,
    correction_csv_rows,
    engine_csv_row_to_reimport,
    rows_to_reimport_csv_text,
)
from mediatracker.integrations.jellyfin_history_import import (
    HistoryLine,
    _series_imdb_for_find,
    apply_import,
    parse_csv_text,
    preview_import,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)
FIXTURE_ZIP = Path(__file__).resolve().parent / "fixtures" / "jellyfin" / "jellyfin-backup-anonyme.zip"
USER_7B = "C881E852-9C27-5FD8-97A2-AD79EE8FC142"


def test_series_imdb_for_find_keeps_full_tt_id():
    assert _series_imdb_for_find("tt12345678") == "tt12345678"
    assert _series_imdb_for_find("tt1234567890") == "tt1234567890"
    assert _series_imdb_for_find("  tt9990001  ") == "tt9990001"


def test_witness_series_does_not_block_episode_import(client, app):
    del client
    db = app.state.session_factory()
    try:
        witness = Media(
            type="series",
            title="Serie Temoin Sans Lien",
            year=2010,
            tvdb_id=999888,
            tmdb_id=777001,
            imdb_id="tt9998880",
            followed=False,
            created_at=NOW,
        )
        db.add(witness)
        db.commit()
        line = HistoryLine(
            line_no=2,
            row_type="episode",
            title="Pilot",
            year=None,
            series_title=None,
            season=1,
            episode=1,
            tmdb_id=None,
            imdb_id="tt9990001",
            tvdb_id=990001,
            jellyfin_item_id=None,
            watched_at=NOW,
            play_count=1,
            source="userdata",
        )
        preview = preview_import(db, [line])
        assert not any("Conflit" in (u.reason or "") for u in preview.unmatched)
        assert len(preview.matched) + len(preview.to_create) == 1
    finally:
        db.close()


def test_second_import_reports_already_ok(client, app, monkeypatch):
    del client

    def fake_find(db, settings, secret_key, line, **kwargs):
        del db, settings, secret_key, kwargs
        return ("Serie Resolue", 880101)

    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_history_import._resolve_series_via_tmdb",
        fake_find,
    )

    db = app.state.session_factory()
    settings = app.state.settings
    try:
        line = HistoryLine(
            line_no=2,
            row_type="episode",
            title="Ep",
            year=None,
            series_title=None,
            season=1,
            episode=3,
            tmdb_id=None,
            imdb_id="tt9990001",
            tvdb_id=990001,
            jellyfin_item_id=None,
            watched_at=NOW,
            play_count=1,
            source="userdata",
        )
        first = apply_import(db, settings, settings.secret_key, [line])
        db.commit()
        assert first.created_media >= 1
        second = apply_import(db, settings, settings.secret_key, [line])
        assert second.created_media == 0
        assert second.already_ok >= 1
        assert not any("Conflit" in (u.reason or "") for u in second.preview.unmatched)
    finally:
        db.close()


def test_episode_correction_tmdb_id_without_series_title_imports(client, app, monkeypatch):
    """B6 : tmdb_id série dans le CSV suffit sans titre de série ni TVDB/IMDB."""
    del client
    from mediatracker.integrations.tmdb import TmdbTitle

    def fake_maybe_fetch(
        db, settings, secret_key, tmdb_id, media_type, *, tmdb_cache=None, import_tx=None, **kwargs
    ):
        del import_tx
        del db, settings, secret_key, tmdb_cache
        if media_type == "series" and tmdb_id == 880202:
            return TmdbTitle(880202, "series", "Serie Corrigee", 2020, None, None, None, None, ())
        return None

    monkeypatch.setattr(
        "mediatracker.integrations.jellyfin_history_import._maybe_fetch",
        fake_maybe_fetch,
    )
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb_media.store_tmdb_episodes",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb_media.fill_next_episode",
        lambda *args, **kwargs: None,
    )

    db = app.state.session_factory()
    settings = app.state.settings
    try:
        csv_text = rows_to_reimport_csv_text(
            [
                {
                    "type": "episode",
                    "title": "Ep sans serie",
                    "year": "",
                    "series_title": "",
                    "season": "1",
                    "episode": "5",
                    "tmdb_id": "880202",
                    "imdb_id": "",
                    "tvdb_id": "",
                    "jellyfin_item_id": "",
                    "watched_at": "2024-01-01T12:00:00Z",
                    "play_count": "1",
                    "source": "userdata",
                    "raison": "test",
                    "drapeaux": "",
                    "confiance": "probable",
                }
            ]
        )
        lines = parse_csv_text(csv_text)
        result = apply_import(db, settings, settings.secret_key, lines)
        db.commit()
        assert result.created_media >= 1
        assert result.applied >= 1
        again = apply_import(db, settings, settings.secret_key, lines)
        assert again.created_media == 0
        assert again.already_ok >= 1
        assert again.applied == 0
        third = apply_import(db, settings, settings.secret_key, lines)
        assert third.applied == 0
        assert third.already_ok >= 1
    finally:
        db.close()


def test_reimport_without_watched_at_keeps_date_and_zero_applied(client, app, monkeypatch):
    del client
    from mediatracker.db.models import Media

    original = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        media = Media(
            type="film",
            title="Film Vu",
            year=2022,
            tmdb_id=880501,
            followed=False,
            created_at=NOW,
        )
        db.add(media)
        db.flush()
        item = MediaItem(
            media_id=media.id,
            title=media.title,
            state="watched",
            watched=True,
            watched_at=original,
        )
        db.add(item)
        db.commit()

        line = HistoryLine(
            line_no=2,
            row_type="movie",
            title="Film Vu",
            year=2022,
            series_title=None,
            season=None,
            episode=None,
            tmdb_id=880501,
            imdb_id=None,
            tvdb_id=None,
            jellyfin_item_id=None,
            watched_at=None,
            play_count=1,
            source="userdata",
        )
        result = apply_import(db, settings, settings.secret_key, [line])
        db.refresh(item)
        assert item.watched_at is not None
        stored = item.watched_at.replace(tzinfo=timezone.utc) if item.watched_at.tzinfo is None else item.watched_at
        assert stored == original
        assert result.applied == 0
        assert result.already_ok >= 1
    finally:
        db.close()


def test_correction_csv_unknown_type_not_exported_as_movie():
    raw = {
        "type": "inconnu",
        "titre_film": "Libelle Detache",
        "titre_serie": "",
        "titre_episode": "",
        "saison": "",
        "episode": "",
        "tmdb": "",
        "imdb": "",
        "tvdb": "",
        "tmdb_serie": "",
        "serie_guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "ids_serie_alias": "sguid:bbbbbbbb",
        "jellyfin_item_id": "",
        "jellyfin_ids": "",
        "date_vue": "2024-01-01T12:00:00.0000000Z",
        "vu": "oui",
        "source": "userdata",
        "confiance": "non importable",
        "raison": "type inconnu",
        "drapeaux": "",
        "evenements_journal": "0",
        "lignes_fusionnees": "1",
    }
    row = engine_csv_row_to_reimport(raw)
    assert row["type"] == "inconnu"
    assert row["serie_guid"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert row["ids_serie_alias"] == "sguid:bbbbbbbb"

    bundle = ExportBundle(rows=[], stats=ExportStats(), csv_rows=[raw])
    rows = correction_csv_rows(bundle)
    assert len(rows) == 1
    assert rows[0]["type"] == "inconnu"


def test_parse_csv_skips_inconnu_type_rows():
    csv_text = (
        "type,title,year,series_title,season,episode,tmdb_id,imdb_id,tvdb_id,"
        "jellyfin_item_id,watched_at,play_count,source\n"
        "inconnu,Titre seul,,,,,,,,,2024-01-01T12:00:00Z,1,userdata\n"
        "episode,Ep,,Serie Demo,1,2,,,,,2024-01-01T12:00:00Z,1,userdata\n"
    )
    lines = parse_csv_text(csv_text)
    assert len(lines) == 1
    assert lines[0].row_type == "episode"


def test_id_conflict_only_when_shared_id_contradicts(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(
            type="series",
            title="Serie Existante",
            year=2020,
            tmdb_id=880001,
            tvdb_id=990001,
            imdb_id="tt9990001",
            followed=False,
            created_at=NOW,
        )
        db.add(media)
        db.commit()
        line = HistoryLine(
            line_no=2,
            row_type="episode",
            title="Ep",
            year=None,
            series_title="Autre titre",
            season=1,
            episode=1,
            tmdb_id=880002,
            imdb_id="tt9990001",
            tvdb_id=None,
            jellyfin_item_id=None,
            watched_at=NOW,
            play_count=1,
            source="userdata",
        )
        preview = preview_import(db, [line])
        assert len(preview.unmatched) == 1
        assert "Conflit" in (preview.unmatched[0].reason or "")
    finally:
        db.close()


@pytest.mark.skipif(not FIXTURE_ZIP.is_file(), reason="fixture zip manquante")
def test_db_session_zip_only_reaches_preview(client, app):
    del app
    login(client)
    headers = csrf(client)
    with FIXTURE_ZIP.open("rb") as handle:
        response = client.post(
            "/api/v1/library/jellyfin-history/db/session",
            headers=headers,
            files={"backup_zip": ("backup.zip", handle.read(), "application/zip")},
        )
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]
    users = client.get(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/users",
        headers=headers,
    )
    assert users.status_code == 200, users.text
    assert any(u["username"] == "utilisateur-test" for u in users.json()["users"])
    preview = client.post(
        f"/api/v1/library/jellyfin-history/db/session/{session_id}/preview?sync=true",
        headers=headers,
        json={"user_id": USER_7B},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body.get("export_sure_movies") == 6
    assert body.get("export_sure_episodes") == 11

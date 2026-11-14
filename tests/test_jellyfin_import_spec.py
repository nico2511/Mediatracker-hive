"""Import historique Jellyfin : spec §8–10 (find, conflits, probables optionnels)."""

from __future__ import annotations

from datetime import datetime, timezone

from mediatracker.db.models import Media
from mediatracker.integrations.jellyfin_db_export import ExportBundle, ExportStats, WatchRow, correction_csv_rows
from mediatracker.integrations.jellyfin_history_import import (
    HistoryLine,
    history_lines_from_watch_rows,
    preview_import,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def test_probables_excluded_by_default_from_watch_rows():
    rows = [
        WatchRow(
            type="movie",
            title="Sûr",
            year=None,
            series_title=None,
            season=None,
            episode=None,
            tmdb_id=1,
            imdb_id=None,
            tvdb_id=None,
            jellyfin_item_id="a",
            watched_at=NOW,
            play_count=1,
            source="userdata",
            confiance="sûr",
        ),
        WatchRow(
            type="movie",
            title="Probable",
            year=None,
            series_title=None,
            season=None,
            episode=None,
            tmdb_id=None,
            imdb_id=None,
            tvdb_id=None,
            jellyfin_item_id="b",
            watched_at=NOW,
            play_count=1,
            source="userdata",
            confiance="probable",
        ),
    ]
    assert len(history_lines_from_watch_rows(rows)) == 1
    assert len(history_lines_from_watch_rows(rows, include_probable=True)) == 2


def test_correction_csv_tmdb_candidate_columns(client, app):
    del client
    bundle = ExportBundle(
        rows=[],
        stats=ExportStats(),
        csv_rows=[
            {
                "utilisateur": "demo",
                "type": "film",
                "titre_film": "Titre Seul Exemple",
                "titre_serie": "",
                "saison": "",
                "episode": "",
                "titre_episode": "",
                "tmdb": "",
                "imdb": "",
                "tvdb": "",
                "tmdb_serie": "",
                "serie_guid": "",
                "ids_serie_alias": "",
                "jellyfin_item_id": "",
                "jellyfin_ids": "",
                "date_vue": "2024-01-01T12:00:00.0000000Z",
                "date_derniere_lecture": "",
                "vu": "oui",
                "source": "userdata",
                "confiance": "probable",
                "raison": "titre seul",
                "drapeaux": "",
                "evenements_journal": "0",
                "lignes_fusionnees": "1",
            }
        ],
    )

    def fake_search(query: str, media_type: str):
        del media_type
        assert query == "Titre Seul Exemple"
        return 880777, "Candidat Exemple", 2019

    db = app.state.session_factory()
    try:
        rows = correction_csv_rows(
            bundle,
            db=db,
            settings=app.state.settings,
            secret_key=app.state.settings.secret_key,
            search_hook=fake_search,
        )
    finally:
        db.close()
    assert len(rows) == 1
    assert rows[0]["candidat_tmdb_id"] == "880777"
    assert rows[0]["candidat_titre"] == "Candidat Exemple"
    assert rows[0]["candidat_annee"] == "2019"
    assert rows[0]["tmdb_id"] == ""


def test_id_conflict_blocks_title_attach(client, app):
    del client
    db = app.state.session_factory()
    try:
        media = Media(
            type="film",
            title="Film Existant",
            year=2020,
            tmdb_id=880001,
            imdb_id="tt9900001",
            followed=False,
            created_at=NOW,
        )
        db.add(media)
        db.commit()
        line = HistoryLine(
            line_no=2,
            row_type="movie",
            title="Film Existant",
            year=2020,
            series_title=None,
            season=None,
            episode=None,
            tmdb_id=880002,
            imdb_id="tt9900001",
            tvdb_id=None,
            jellyfin_item_id=None,
            watched_at=NOW,
            play_count=1,
            source="userdata",
        )
        preview = preview_import(db, [line])
        assert len(preview.matched) == 0
        assert len(preview.unmatched) == 1
        assert "Conflit" in (preview.unmatched[0].reason or "")
    finally:
        db.close()

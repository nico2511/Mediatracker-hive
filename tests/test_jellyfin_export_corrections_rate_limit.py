"""Export corrections CSV must survive TMDB 429 (B7)."""

from __future__ import annotations

from datetime import datetime, timezone

from tests.conftest import csrf, login

from mediatracker.integrations.jellyfin_db_export import ExportBundle, ExportStats, correction_csv_rows
from mediatracker.integrations.tmdb import TmdbError

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def _probable_bundle(*titles: str) -> ExportBundle:
    csv_rows = []
    for idx, title in enumerate(titles):
        csv_rows.append(
            {
                "utilisateur": "demo",
                "type": "film",
                "titre_film": title,
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
                "jellyfin_item_id": f"id-{idx}",
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
        )
    return ExportBundle(rows=[], stats=ExportStats(), csv_rows=csv_rows)


class _RateLimitedSearch:
    calls = 0

    def search(self, query: str, media_type: str):
        del query, media_type
        _RateLimitedSearch.calls += 1
        raise TmdbError("RATE_LIMIT", "quota")

    def close(self):
        return None


def test_correction_csv_rows_survives_rate_limit(client, app, monkeypatch):
    del client
    _RateLimitedSearch.calls = 0
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (_RateLimitedSearch(), True),
    )
    db = app.state.session_factory()
    bundle = _probable_bundle("Exemple A", "Exemple B")
    try:
        rows = correction_csv_rows(
            bundle,
            db=db,
            settings=app.state.settings,
            secret_key=app.state.settings.secret_key,
        )
    finally:
        db.close()
    assert len(rows) == 2
    assert rows[0]["candidat_tmdb_id"] == ""
    assert rows[1]["candidat_tmdb_id"] == ""
    assert _RateLimitedSearch.calls == 1


def test_export_corrections_csv_route_returns_200_on_rate_limit(client, app, monkeypatch):
    login(client)
    bundle = _probable_bundle("Exemple route")
    monkeypatch.setattr(
        "mediatracker.api.v1.jellyfin_history._export_bundle_for_session",
        lambda request, session_id, user_id: bundle,
    )
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok")
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls: (_RateLimitedSearch(), True),
    )
    response = client.post(
        "/api/v1/library/jellyfin-history/db/session/fake-session/export-corrections.csv",
        json={"user_id": "11111111-1111-1111-1111-111111111111"},
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    assert "Exemple route" in response.text
    assert "candidat_tmdb_id" in response.text.splitlines()[0]

"""Sonde sources : année TMDB ou suffixe titre, pas d'année fictive fixe."""

from mediatracker.core.source_probe import _stub_media, enrich_probe_target, parse_probe_query
from mediatracker.integrations.tmdb import TmdbTitle


def test_parse_probe_query_strips_parenthetical_year():
    target = parse_probe_query("Film Démo (2031)")
    assert target.title == "Film Démo"
    assert target.year == 2031
    media = _stub_media(target)
    assert media.year == 2031


def test_stub_media_year_none_without_hint():
    target = parse_probe_query("Titre neutre")
    media = _stub_media(target)
    assert media.year is None


def test_enrich_probe_target_uses_tmdb_year_and_original(client, app, monkeypatch):
    def fake_fetch(_db, _settings, tmdb_id, media_type):
        assert tmdb_id == 900101
        assert media_type == "film"
        return TmdbTitle(
            900101,
            "film",
            "Titre Démo Fictif",
            2031,
            None,
            None,
            None,
            None,
            (),
            original_title="Original Démo Fictif",
        )

    monkeypatch.setattr("mediatracker.core.tmdb.fetch.fetch_title", fake_fetch)
    monkeypatch.setattr("mediatracker.core.tmdb.imdb.ensure_imdb", lambda *_a, **_k: "tt9999999")

    target = parse_probe_query("Entrée utilisateur", tmdb_id=900101)
    db = app.state.session_factory()
    try:
        enriched = enrich_probe_target(db, app.state.settings, target)
    finally:
        db.close()
    media = _stub_media(enriched)
    assert media.year == 2031
    assert media.title == "Titre Démo Fictif"
    assert media.original_title == "Original Démo Fictif"
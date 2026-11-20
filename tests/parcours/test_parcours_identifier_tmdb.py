"""Parcours et régressions : une seule règle d'identification TMDB (exact_hit)."""

from __future__ import annotations

from dataclasses import dataclass

from tests.conftest import csrf, login

from mediatracker.core.catalog import choose_tmdb_hit
from mediatracker.core.catalog_poster import tmdb_search_title
from mediatracker.core.tmdb import candidate_payload, exact_hit, glance_hit, hit_years_differ
from mediatracker.integrations.jellyfin_history_tmdb import resolve_exact_tmdb_candidate
from mediatracker.integrations.tmdb import TmdbHit
from mediatracker.library.parse import ParsedName


def _parsed(title: str, year: int | None, media_type: str = "film") -> ParsedName:
    return ParsedName(
        title=title,
        year=year,
        season=None,
        episode=None,
        tmdb_id=None,
        quality=None,
        language=None,
        media_type=media_type,
    )


def test_parcours_identify_exact_match_requires_year_when_both_have_one():
    parsed = _parsed("Exemple", 2020)
    hits = [
        TmdbHit(1, "film", "Exemple", 2019, None, None),
        TmdbHit(2, "film", "Exemple", 2020, None, None),
    ]
    assert exact_hit(hits, parsed).tmdb_id == 2
    assert exact_hit(hits, _parsed("Exemple", 2018)) is None


def test_parcours_identify_homonym_years_differ():
    parsed = _parsed("Exemple", 2020)
    hits = [
        TmdbHit(1, "film", "Exemple", 2019, None, None),
        TmdbHit(2, "film", "Exemple", 2020, None, None),
    ]
    assert hit_years_differ(hits, parsed) is True


def test_parcours_identify_no_exact_match_never_picks_first_at_random():
    parsed = _parsed("Exemple Cible", 2024)
    hits = [
        TmdbHit(10, "film", "Autre", 2024, None, None),
        TmdbHit(11, "film", "Exemple Cible", 2023, None, None),
    ]
    assert exact_hit(hits, parsed) is None


def test_parcours_identify_no_results():
    parsed = _parsed("Introuvable", 2020)
    assert exact_hit([], parsed) is None


def test_parcours_identify_catalog_year_rule():
    pilot_hit = TmdbHit(90001, "film", "Archipel Pilote", 2021, "Désert.", "https://image.tmdb.org/p.jpg")
    legacy_hit = TmdbHit(90002, "film", "Archipel Pilote", 1984, "Autre.", None)
    assert choose_tmdb_hit("Archipel Pilote", 2021, [legacy_hit, pilot_hit]) is pilot_hit
    assert choose_tmdb_hit("Archipel Pilote", 1999, [pilot_hit]) is None
    assert choose_tmdb_hit("Palais Démo", 2024, [TmdbHit(1, "series", "Palais Démo", 2024, None, None)]) is not None


def test_parcours_identify_catalog_poster_uses_cleaned_title():
    title = "Exemple Titre - FRENCH"
    query = tmdb_search_title(title)
    hit = TmdbHit(42, "film", "Exemple Titre", 2025, None, "https://image.tmdb.org/t/p/w500/exemple.jpg")
    assert choose_tmdb_hit(title, 2025, [hit]) is None
    assert choose_tmdb_hit(query, 2025, [hit]) is not None


def test_parcours_identify_strict_title_not_original_alias():
    hits = [
        TmdbHit(1, "film", "Exemple A", 2000, None, None),
        TmdbHit(2, "film", "Exemple B", 1998, None, None),
        TmdbHit(3, "film", "Exemple C 2", 2026, None, None),
    ]
    assert choose_tmdb_hit("Exemple C 2", 2026, hits).tmdb_id == 3
    assert choose_tmdb_hit("Exemple alias", 2026, hits) is None


def test_parcours_identify_glance_enriches_candidates():
    hit = TmdbHit(3, "series", "Exemple B", 2021, "Texte", "https://image.tmdb.org/t/p/w500/y.jpg")

    @dataclass
    class _Client:
        glanced: bool = False

        def glance(self, tmdb_id: int, media_type: str):
            self.glanced = True
            return TmdbHit(
                42,
                "series",
                "Exemple A",
                2024,
                "Résumé court.",
                "https://image.tmdb.org/t/p/w500/x.jpg",
            )

    client = _Client()
    detailed = glance_hit(client, hit)
    assert client.glanced is True
    payload = candidate_payload(detailed)
    assert payload["tmdb_id"] == 42
    assert payload["poster_url"] == "https://image.tmdb.org/t/p/w92/x.jpg"


class _SearchClient:
    def __init__(self, hits: list[TmdbHit]) -> None:
        self._hits = hits

    def search(self, query: str, media_type: str) -> list[TmdbHit]:
        del query, media_type
        return self._hits

    def close(self) -> None:
        return None


def test_parcours_jellyfin_export_candidate_only_on_exact_match(client, app, monkeypatch):
    del client
    db = app.state.session_factory()
    settings = app.state.settings
    wrong_first = [
        TmdbHit(99, "film", "Film-exact", 2019, None, None),
        TmdbHit(100, "film", "Film-exact", 2020, None, None),
    ]
    monkeypatch.setattr(
        "mediatracker.core.tmdb.token.read_tmdb_token",
        lambda db, key: "tok",
    )
    monkeypatch.setattr(
        "mediatracker.integrations.tmdb.borrow_tmdb",
        lambda token, cls, **_: (_SearchClient(wrong_first), True),
    )
    try:
        assert (
            resolve_exact_tmdb_candidate(
                db,
                settings,
                settings.secret_key,
                query="Film-exact",
                media_type="film",
                year=2020,
            )
            is not None
        )
        assert (
            resolve_exact_tmdb_candidate(
                db,
                settings,
                settings.secret_key,
                query="Film-exact",
                media_type="film",
                year=2018,
            )
            is None
        )
    finally:
        db.close()


def test_parcours_patch_manual_tmdb_id_clears_identify(client, app, monkeypatch):
    login(client)
    db = app.state.session_factory()
    from mediatracker.db.models import Media

    media = Media(
        type="series",
        title="Serie-identify",
        followed=False,
        tmdb_identify_status="pending",
        tmdb_identify_candidates=[{"tmdb_id": 1, "title": "X"}],
        last_repair_summary="À identifier TMDB.",
    )
    db.add(media)
    db.commit()
    mid = media.id
    db.close()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    r = client.patch(f"/api/v1/media/{mid}", json={"tmdb_id": 88109}, headers=csrf(client))
    assert r.status_code == 200, r.text
    db = app.state.session_factory()
    row = db.get(Media, mid)
    assert row.tmdb_id == 88109
    assert row.tmdb_identify_status is None
    assert row.tmdb_identify_candidates is None
    db.close()

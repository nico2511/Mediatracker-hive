"""Sélection et exposition API de la bande-annonce TMDB."""

from __future__ import annotations

from tests.conftest import csrf, login

from mediatracker.integrations.tmdb_enrichment import pick_trailer_from_videos


def _videos(*rows: dict) -> dict:
    return {"results": list(rows)}


def test_pick_trailer_official_fr_before_en():
    block = _videos(
        {"site": "YouTube", "key": "en-key", "type": "Trailer", "official": True, "iso_639_1": "en"},
        {"site": "YouTube", "key": "fr-key", "type": "Trailer", "official": True, "iso_639_1": "fr"},
    )
    picked = pick_trailer_from_videos(block)
    assert picked is not None
    assert picked.youtube_key == "fr-key"
    assert picked.trailer_kind == "official"
    assert picked.language == "fr"


def test_pick_trailer_unofficial_after_official():
    block = _videos(
        {"site": "YouTube", "key": "unoff-fr", "type": "Trailer", "official": False, "iso_639_1": "fr"},
        {"site": "YouTube", "key": "off-en", "type": "Trailer", "official": True, "iso_639_1": "en"},
    )
    picked = pick_trailer_from_videos(block)
    assert picked is not None
    assert picked.youtube_key == "off-en"
    assert picked.trailer_kind == "official"


def test_pick_trailer_teaser_last_resort():
    block = _videos(
        {"site": "YouTube", "key": "clip-fr", "type": "Clip", "official": True, "iso_639_1": "fr"},
        {"site": "YouTube", "key": "tease-en", "type": "Teaser", "official": False, "iso_639_1": "en"},
    )
    picked = pick_trailer_from_videos(block)
    assert picked is not None
    assert picked.youtube_key == "tease-en"
    assert picked.trailer_kind == "teaser"


def test_pick_trailer_ignores_non_youtube():
    block = _videos(
        {"site": "Vimeo", "key": "vvv", "type": "Trailer", "official": True, "iso_639_1": "fr"},
    )
    assert pick_trailer_from_videos(block) is None


def test_pick_trailer_empty():
    assert pick_trailer_from_videos(None) is None
    assert pick_trailer_from_videos({"results": []}) is None


def test_pick_trailer_null_language_last_resort():
    block = _videos(
        {"site": "YouTube", "key": "no-lang", "type": "Trailer", "official": True, "iso_639_1": None},
        {"site": "YouTube", "key": "teaser-fr", "type": "Teaser", "official": False, "iso_639_1": "fr"},
    )
    picked = pick_trailer_from_videos(block)
    assert picked is not None
    assert picked.youtube_key == "teaser-fr"
    block2 = _videos(
        {"site": "YouTube", "key": "no-lang", "type": "Trailer", "official": True, "iso_639_1": None},
    )
    picked2 = pick_trailer_from_videos(block2)
    assert picked2 is not None
    assert picked2.youtube_key == "no-lang"
    assert picked2.language is None


def test_media_out_exposes_trailer_fields(client, app, monkeypatch):
    login(client)
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "")
    created = client.post(
        "/api/v1/media",
        json={"type": "film", "title": "Exemple-trailer", "year": 2020},
        headers=csrf(client),
    )
    assert created.status_code == 201
    mid = created.json()["id"]
    db = app.state.session_factory()
    from mediatracker.db.models import Media

    row = db.get(Media, mid)
    row.tmdb_trailer_youtube_key = "demo-key-01"
    row.tmdb_trailer_kind = "official"
    row.tmdb_trailer_language = "fr"
    db.commit()
    db.close()
    detail = client.get(f"/api/v1/media/{mid}").json()
    assert detail["tmdb_trailer_youtube_key"] == "demo-key-01"
    assert detail["tmdb_trailer_kind"] == "official"
    assert detail["tmdb_trailer_language"] == "fr"
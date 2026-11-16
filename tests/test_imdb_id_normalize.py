"""Normalisation IMDb partagée (sonde sources, addons Stremio)."""

import httpx
import pytest
from tests.conftest import csrf, login

from mediatracker.core.source_probe import parse_probe_query
from mediatracker.core.tmdb.imdb import imdb_id_for_stremio, normalize_imdb_id
from mediatracker.providers.base import EpisodeQuery, SourcePreferences
from mediatracker.providers.sources.comet import CometPathCache
from mediatracker.providers.sources.throttle import SourcePacer
from mediatracker.providers.sources.uwuplus import UwuPlusSource


def test_normalize_imdb_id_accepts_digits_tt_and_spaces():
    assert normalize_imdb_id("9999999") == "tt9999999"
    assert normalize_imdb_id("tt9999999") == "tt9999999"
    assert normalize_imdb_id("TT 9999999") == "tt9999999"
    assert normalize_imdb_id("  tt  9999999  ") == "tt9999999"


def test_normalize_imdb_id_rejects_invalid():
    with pytest.raises(ValueError, match="invalide"):
        normalize_imdb_id("tt12")
    with pytest.raises(ValueError, match="manquant"):
        normalize_imdb_id("   ")


def test_parse_probe_query_normalizes_imdb_id():
    target = parse_probe_query("Film Démo", imdb_id="9999999")
    assert target.imdb_id == "tt9999999"


def test_probe_sources_endpoint_rejects_invalid_imdb(client):
    login(client)
    response = client.post(
        "/api/v1/settings/sources/probe",
        json={"query": "Film Démo", "imdb_id": "nope"},
        headers=csrf(client),
    )
    assert response.status_code == 422
    assert "IMDb" in response.json()["detail"]


def test_uwuplus_links_for_uses_normalized_imdb_in_url():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text='{"streams":[]}')

    source = UwuPlusSource(
        "https://uwu-fictif.example",
        transport=httpx.MockTransport(handler),
        cache=CometPathCache(revalidate_seconds=3600.0),
        pacer=SourcePacer(0),
    )
    query = EpisodeQuery(
        title="Labo Démo",
        year=2026,
        media_type="film",
        imdb_id="9999999",
    )
    try:
        source.links_for(query, SourcePreferences())
    finally:
        source.close()
    assert seen == ["https://uwu-fictif.example/stream/movie/tt9999999.json"]


def test_imdb_id_for_stremio_returns_none_when_invalid():
    assert imdb_id_for_stremio("bad") is None
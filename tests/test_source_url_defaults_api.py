"""Réponse GET /settings inclut les URL par défaut des sources."""

from tests.conftest import login

from mediatracker.core.public_source_urls import DEFAULT_COMET_INSTANCE_URLS, DEFAULT_TORRENTIO_BASE_URL


def test_settings_includes_source_defaults(client):
    login(client)
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    body = response.json()
    assert "source_defaults" in body
    defaults = body["source_defaults"]
    assert defaults["torrentio_base_url"] == DEFAULT_TORRENTIO_BASE_URL
    assert defaults["comet_base_urls"] == list(DEFAULT_COMET_INSTANCE_URLS)
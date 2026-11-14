"""Assistant : enregistrement des URLs de sources."""

from sqlalchemy.orm import Session
from tests.conftest import csrf

from mediatracker.db.models import Setting
from mediatracker.providers.sources.loading import source_base_url


def _sign_in_wizard(wizard_client):
    application, client = wizard_client
    token = application.state.setup_bootstrap_token
    created = client.post(
        "/api/v1/setup/account",
        json={"username": "admin", "password": "mot-de-passe", "setup_token": token},
    )
    assert created.status_code == 201, created.text
    return application, client


def test_setup_sources_empty_keeps_defaults(wizard_client):
    _application, client = _sign_in_wizard(wizard_client)
    response = client.post("/api/v1/setup/sources", json={}, headers=csrf(client))
    assert response.status_code == 200, response.text
    assert response.json()["saved"] == []


def test_setup_sources_rejects_bad_url(wizard_client):
    _application, client = _sign_in_wizard(wizard_client)
    response = client.post(
        "/api/v1/setup/sources",
        json={"wawacity_base_url": "pas-une-url"},
        headers=csrf(client),
    )
    assert response.status_code == 422


def test_setup_sources_persists_urls(wizard_client):
    application, client = _sign_in_wizard(wizard_client)
    wawa = "https://wawacity-neutre.example"
    torr = "https://torrentio.strem.fun"
    comet = "https://comet.feels.legal"
    free = "https://free-telecharger-neutre.example"
    response = client.post(
        "/api/v1/setup/sources",
        json={
            "wawacity_base_url": wawa,
            "torrentio_base_url": torr,
            "comet_base_urls": comet,
            "free_telecharger_base_url": free,
        },
        headers=csrf(client),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert "wawacity_base_url" in body["saved"]
    assert "torrentio_base_url" in body["saved"]
    assert "comet_base_urls" in body["saved"]
    assert "free_telecharger_base_url" in body["saved"]
    with Session(application.state.engine) as session:
        settings = application.state.settings
        assert source_base_url(session, settings, "wawacity").rstrip("/") == wawa.rstrip("/")
        assert source_base_url(session, settings, "torrentio").rstrip("/") == torr.rstrip("/")
        assert source_base_url(session, settings, "free_telecharger").rstrip("/") == free.rstrip("/")
        row = session.get(Setting, "comet_base_urls")
        assert row is not None
        assert comet in (row.value or "")

"""Jeton d'amorçage pour la création du premier compte."""

import pytest
from fastapi.testclient import TestClient

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.security.setup_bootstrap import token_matches


def _wizard_settings(tmp_path, **overrides):
    base = {
        "secret_key": "wizard-secret-key-0123456789",
        "admin_password": None,
        "setup_token": None,
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "data_dir": tmp_path / "data",
        "config_dir": tmp_path / "config",
        "disk_min_free_gb": 0,
    }
    base.update(overrides)
    return Settings(**base)


def _account_payload(token: str = "", username: str = "nico", password: str = "mot-de-passe"):
    return {"username": username, "password": password, "setup_token": token}


@pytest.fixture
def wizard_client(tmp_path):
    application = create_app(_wizard_settings(tmp_path))
    with TestClient(application) as client:
        yield application, client
    application.state.engine.dispose()


def test_token_matches_rejects_missing_expected():
    assert token_matches(None, "") is False
    assert token_matches("", "") is False
    assert token_matches("   ", "jeton") is False


def test_setup_account_refused_when_bootstrap_token_state_cleared(wizard_client, monkeypatch):
    application, client = wizard_client
    warnings: list[str] = []

    def _capture_warning(message, *args):
        warnings.append(message % args if args else message)

    monkeypatch.setattr("mediatracker.security.setup_bootstrap.logger.warning", _capture_warning)
    application.state.setup_bootstrap_token = None
    response = client.post("/api/v1/setup/account", json=_account_payload(""))
    assert response.status_code == 403
    assert application.state.setup_bootstrap_token
    assert len(warnings) == 1


def test_setup_account_refused_without_token(wizard_client):
    _application, client = wizard_client
    response = client.post("/api/v1/setup/account", json=_account_payload())
    assert response.status_code == 403
    assert "jeton" in response.json()["detail"].lower()


def test_setup_account_refused_with_wrong_token(wizard_client):
    _application, client = wizard_client
    response = client.post("/api/v1/setup/account", json=_account_payload("mauvais-jeton"))
    assert response.status_code == 403


def test_setup_account_accepts_env_setup_token(tmp_path):
    env_token = "jeton-env-fictif"
    application = create_app(_wizard_settings(tmp_path, setup_token=env_token))
    with TestClient(application) as client:
        assert application.state.setup_bootstrap_token == env_token
        response = client.post(
            "/api/v1/setup/account",
            json=_account_payload(env_token),
        )
        assert response.status_code == 201, response.text
    application.state.engine.dispose()


def test_setup_account_accepts_generated_token(wizard_client):
    application, client = wizard_client
    token = application.state.setup_bootstrap_token
    assert token
    response = client.post("/api/v1/setup/account", json=_account_payload(token))
    assert response.status_code == 201, response.text


def test_bootstrap_token_logged_once_when_no_account(tmp_path, monkeypatch):
    warnings: list[str] = []

    def _capture_warning(message, *args):
        warnings.append(message % args if args else message)

    monkeypatch.setattr("mediatracker.security.setup_bootstrap.logger.warning", _capture_warning)
    application = create_app(_wizard_settings(tmp_path))
    with TestClient(application) as client:
        assert application.state.setup_bootstrap_token
        response = client.post(
            "/api/v1/setup/account",
            json=_account_payload(application.state.setup_bootstrap_token),
        )
        assert response.status_code == 201
    assert len(warnings) == 1
    assert "jeton d'installation" in warnings[0].lower()
    application.state.engine.dispose()


def test_bootstrap_token_not_logged_when_account_exists(tmp_path, monkeypatch):
    warnings: list[str] = []

    def _capture_warning(message, *args):
        warnings.append(message % args if args else message)

    monkeypatch.setattr("mediatracker.security.setup_bootstrap.logger.warning", _capture_warning)
    application = create_app(
        _wizard_settings(tmp_path, admin_password="mot-de-passe-test", admin_username="admin")
    )
    with TestClient(application) as client:
        assert client.get("/api/v1/setup/status").json()["needs_password"] is False
    assert warnings == []
    application.state.engine.dispose()


def test_initialized_instance_unchanged(client):
    response = client.post(
        "/api/v1/setup/account",
        json=_account_payload("n-importe-quoi"),
    )
    assert response.status_code == 409

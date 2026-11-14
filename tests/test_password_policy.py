"""Politique de mot de passe (création et changement)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mediatracker.api.app import create_app
from mediatracker.api.errors import Problem
from mediatracker.config import Settings
from mediatracker.security.password_policy import validate_new_password


@pytest.mark.parametrize(
    "password",
    [
        "Choix-Solide-42!",
        "Truite-Bleue#97",
        "Signal-Renard$44",
        "Equinoxe-Pluie%82",
    ],
)
def test_password_accepts_strong_choice(password):
    validate_new_password(password, username="operateur")


@pytest.mark.parametrize(
    "password",
    [
        "12345678",
        "123456789012",
        "12345678abcd",
        "abcdef123456",
        "Password1234",
        "Motdepasse12",
        "aaaaaa111111",
        "111111aaaaaa",
        "aaaaaaaaaaaa",
        "Azertyuiop12",
        "Qwertyuiop99",
    ],
)
def test_password_rejects_weak(password):
    with pytest.raises(Problem) as exc:
        validate_new_password(password, username="operateur")
    assert exc.value.status == 422


def test_password_rejects_username_embedded():
    with pytest.raises(Problem) as exc:
        validate_new_password("Kiosque-Alice-44", username="alice")
    assert exc.value.status == 422
    assert "nom d'utilisateur" in exc.value.detail.lower()


def test_change_password_rejects_weak(client):
    from tests.conftest import csrf, login

    login(client)
    resp = client.post(
        "/api/v1/auth/password",
        json={"current_password": "test-admin-password", "new_password": "12345678"},
        headers=csrf(client),
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]


def test_setup_account_short_password_returns_french_policy_message(tmp_path):
    application = create_app(
        Settings(
            secret_key="wizard-secret-key-0123456789",
            admin_password=None,
            database_url=f"sqlite:///{tmp_path / 'policy.db'}",
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            disk_min_free_gb=0,
        )
    )
    with TestClient(application) as client:
        token = application.state.setup_bootstrap_token
        resp = client.post(
            "/api/v1/setup/account",
            json={"username": "nico", "password": "court", "setup_token": token},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert "detail" in body
        assert "12" in body["detail"]
        assert body.get("errors") is None
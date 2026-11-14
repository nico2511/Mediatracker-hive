"""First-run account creation and Jellyfin library path warnings."""

from fastapi.testclient import TestClient
from tests.conftest import csrf

from mediatracker.api.app import create_app
from mediatracker.config import Settings
from mediatracker.integrations.paths import library_path_warnings


def test_same_container_path_is_silent():
    warnings = library_path_warnings(
        "/data/media/Films",
        "/data/media/Series",
        [
            {"name": "Films", "collection_type": "movies", "locations": ["/data/media/Films"]},
            {"name": "Séries", "collection_type": "tvshows", "locations": ["/data/media/Series"]},
        ],
    )
    assert warnings == []


def test_different_jellyfin_path_warns_until_the_prefix_matches():
    folders = [{"name": "Films", "collection_type": "movies", "locations": ["/media/Films"]}]
    warnings = library_path_warnings("/data/media/Films", "/data/media/Series", folders)
    assert len(warnings) == 1
    assert "préfixe" in warnings[0]
    assert library_path_warnings("/data/media/Films", "/data/media/Series", folders, prefix="/media") == []


def test_wizard_creates_the_account_and_scans(tmp_path):
    application = create_app(
        Settings(
            secret_key="wizard-secret-key-0123456789",
            admin_password=None,
            database_url=f"sqlite:///{tmp_path / 'app.db'}",
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            disk_min_free_gb=0,
        )
    )
    with TestClient(application) as client:
        status = client.get("/api/v1/setup/status")
        assert status.status_code == 200
        assert status.json() == {"needs_password": True, "completed": False}
        token = application.state.setup_bootstrap_token
        assert token
        created = client.post(
            "/api/v1/setup/account",
            json={"username": "nico", "password": "mot-de-passe", "setup_token": token},
        )
        assert created.status_code == 201, created.text
        assert created.json()["username"] == "nico"
        probe = client.post(
            "/api/v1/setup/probe",
            json={"target": "alldebrid", "value": ""},
            headers=csrf(client),
        )
        assert probe.status_code == 200
        assert probe.json()["ok"] is False
        done = client.post("/api/v1/setup/complete", headers=csrf(client))
        assert done.status_code == 200, done.text
        assert done.json()["completed"] is True
        assert done.json()["seen"] == 0
        assert client.get("/api/v1/setup/status").json()["completed"] is True
        (tmp_path / "data" / "media" / "Films").mkdir(parents=True, exist_ok=True)
    application.state.engine.dispose()


def test_second_account_is_refused(client):
    response = client.post(
        "/api/v1/setup/account",
        json={"username": "autre", "password": "mot-de-passe"},
    )
    assert response.status_code == 409

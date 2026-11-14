import json
from pathlib import Path

from mediatracker.api.app import create_app
from mediatracker.config import Settings


def test_openapi_snapshot_matches(tmp_path):
    application = create_app(
        Settings(
            secret_key="openapi-snapshot-secret",
            database_url=f"sqlite:///{tmp_path / 'openapi.db'}",
            data_dir=tmp_path,
            admin_password=None,
        )
    )
    current = json.dumps(application.openapi(), indent=2, sort_keys=True) + "\n"
    snapshot = Path(__file__).resolve().parents[1] / "openapi.json"
    assert snapshot.read_text(encoding="utf-8") == current

#!/usr/bin/env python3
"""SQLite minimal (admin + setup) pour captures Playwright PR #433."""

from __future__ import annotations

import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
sys.path.insert(0, str(root))

from fastapi.testclient import TestClient  # noqa: E402
from tests.conftest import TEST_ADMIN_PASSWORD, csrf, login  # noqa: E402
from tests.test_hive_t6 import NOW  # noqa: E402

from mediatracker.api.app import create_app  # noqa: E402
from mediatracker.config import Settings  # noqa: E402
from mediatracker.db.models import Setting  # noqa: E402

SETUP_KEY = "system.setup_completed"
OUT = Path(os.environ.get("PR433_DEMO_DIR", f"/tmp/pr433-demo-{os.getpid()}"))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        secret_key="pr433-demo-secret-key-012345",
        admin_username="admin",
        admin_password=TEST_ADMIN_PASSWORD,
        database_url=f"sqlite:///{OUT / 'app.db'}",
        data_dir=OUT / "data",
        config_dir=OUT / "config",
        disk_min_free_gb=0,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        login(client)
        db = app.state.session_factory()
        try:
            row = db.get(Setting, SETUP_KEY)
            if row is None:
                db.add(Setting(key=SETUP_KEY, value="1", is_secret=False, updated_at=NOW))
            else:
                row.value = "1"
            db.commit()
        finally:
            db.close()
        client.patch(
            "/api/v1/settings",
            json={
                "values": {
                    "hive_enabled": True,
                    "hive_activation_name": "demo",
                    "hive_public_address": "https://mediatracker.example.com",
                }
            },
            headers=csrf(client),
        )
    print(f"pr433 seed ok {OUT}")


if __name__ == "__main__":
    main()
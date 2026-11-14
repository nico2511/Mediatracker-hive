"""Write the versioned OpenAPI snapshot. Run from repo root: python scripts/export_openapi.py"""

import json
from pathlib import Path

from mediatracker.api.app import create_app
from mediatracker.config import Settings


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    application = create_app(
        Settings(
            secret_key="openapi-snapshot-secret",
            database_url=f"sqlite:///{root / '.openapi.db'}",
            data_dir=root,
            admin_password=None,
        )
    )
    payload = json.dumps(application.openapi(), indent=2, sort_keys=True) + "\n"
    (root / "openapi.json").write_text(payload, encoding="utf-8")
    application.state.engine.dispose()


if __name__ == "__main__":
    main()

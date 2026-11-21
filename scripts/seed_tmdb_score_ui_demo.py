#!/usr/bin/env python3
"""Données fictives pour captures note TMDB (Médias, fiche)."""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
sys.path.insert(0, str(root))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select

from mediatracker.api.app import create_app  # noqa: E402
from mediatracker.config import Settings  # noqa: E402
from mediatracker.db.models import Media, Setting  # noqa: E402

OUT = Path(os.environ.get("TMDB_SCORE_DEMO_DIR", f"/tmp/tmdb-score-demo-{os.getpid()}"))
MARKER = ".tmdb_score_ui_demo_marker"
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Variable d'environnement requise : {name}")
    return value


def _reset_dir() -> None:
    marker = OUT / MARKER
    if OUT.exists() and not marker.is_file():
        raise RuntimeError(f"Répertoire déjà présent sans marqueur : {OUT}")
    OUT.mkdir(parents=True, exist_ok=True)
    marker.write_text("tmdb-score-demo\n", encoding="utf-8")
    db = OUT / "app.db"
    if db.is_file():
        db.unlink()
    for sub in ("data", "config"):
        p = OUT / sub
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)


def main() -> None:
    secret_key = _require_env("TMDB_SCORE_SECRET_KEY")
    admin_password = _require_env("TMDB_SCORE_ADMIN_PASSWORD")
    _reset_dir()
    settings = Settings(
        secret_key=secret_key,
        admin_username="admin",
        admin_password=admin_password,
        database_url=f"sqlite:///{OUT / 'app.db'}",
        data_dir=OUT / "data",
        config_dir=OUT / "config",
        disk_min_free_gb=0,
    )
    app = create_app(settings)
    with TestClient(app):
        db = app.state.session_factory()
        try:
            titles = [
                ("film", "Film exemple 3", 2021, 880033, 8.1, 1240),
                ("series", "Série exemple 1", 2019, 880011, 7.4, 520),
                ("film", "Film exemple 4", 2020, 880044, 7.2, 0),
                ("series", "Série exemple 2", 2018, 880022, None, None),
            ]
            media_ids: list[int] = []
            for media_type, title, year, tmdb_id, avg, count in titles:
                row = Media(
                    type=media_type,
                    title=title,
                    year=year,
                    tmdb_id=tmdb_id,
                    followed=True,
                    on_hold=False,
                    created_at=NOW,
                )
                if avg is not None:
                    row.tmdb_vote_average = avg
                if count is not None:
                    row.tmdb_vote_count = count
                db.add(row)
                db.flush()
                media_ids.append(row.id)

            if not db.scalar(select(Setting).where(Setting.key == "system.setup_completed")):
                db.add(Setting(key="system.setup_completed", value="1"))
            db.commit()
        finally:
            db.close()

    meta = {
        "media_fiche_id": media_ids[0],
        "media_list_ids": media_ids,
    }
    (OUT / "seed-meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps({"demo_dir": str(OUT), **meta}))


if __name__ == "__main__":
    main()
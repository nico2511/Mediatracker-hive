#!/usr/bin/env python3
"""Captures Santé › Activité (carte métro) en 1440 et 390 px avec passages fictifs."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mediatracker.api.app import create_app  # noqa: E402
from mediatracker.config import Settings  # noqa: E402
from mediatracker.db.migrate import run_migrations  # noqa: E402
from mediatracker.db.models import Setting  # noqa: E402
from mediatracker.metro.collection import set_collection_duration  # noqa: E402
from mediatracker.metro.record import (  # noqa: E402
    flush_pass_buffer,
    load_pass_collection_flag,
    record_task_run,
    reset_metro_instrumentation_state,
)

ARTIFACTS = Path("/opt/cursor/artifacts")
NOW = datetime.now(timezone.utc)
DEFAULT_ADMIN = "Choix-Solide-42!"
DEFAULT_SECRET = "capture-metro-local-secret-key-32c"


def _env(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _seed_metro(factory) -> None:
    reset_metro_instrumentation_state()
    with factory() as db:
        set_collection_duration(db, "24h")
        db.merge(Setting(key="system.setup_completed", value="1", is_secret=False, updated_at=NOW))
        db.commit()
    load_pass_collection_flag(factory)
    samples = (
        ("RNG5", "C16", "ok", 1200, "download:701"),
        ("ADB1", "C06", "blocked", 900, "download:702"),
        ("DL1", "C06", "ok", 400, "download:703"),
        ("JF4", "C49", "ok", 800, "download:704"),
        ("TMDB3", "C02", "ok", 500, "download:705"),
    )
    for index, (station, edge_id, outcome, duration_ms, subject_ref) in enumerate(samples):
        started = NOW - timedelta(hours=23 - index * 3)
        finished = started + timedelta(milliseconds=duration_ms)
        record_task_run(
            station=station,
            trigger="event" if index % 2 == 0 else "schedule",
            outcome=outcome,
            duration_ms=duration_ms,
            edge_id=edge_id,
            subject_ref=subject_ref,
            blocked_by="ADB1:AUTH_BLOCKED" if outcome == "blocked" else None,
            started_at=started,
            finished_at=finished if outcome != "blocked" else None,
        )
    record_task_run(
        station="DL1",
        trigger="schedule",
        outcome="blocked",
        edge_id="C06",
        blocked_by="ADB1:AUTH_BLOCKED",
        subject_ref="download:block-live",
        started_at=NOW - timedelta(minutes=3),
        finished_at=None,
    )
    flush_pass_buffer(factory)


def main() -> None:
    admin_password = _env("MEDIATRACKER_CAPTURE_ADMIN_PASSWORD", DEFAULT_ADMIN)
    secret_key = _env("MEDIATRACKER_CAPTURE_SECRET_KEY", DEFAULT_SECRET)
    os.environ["SECRET_KEY"] = secret_key
    tmp = ROOT / ".capture-metro"
    if tmp.exists():
        import shutil

        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    db_path = tmp / "app.db"
    settings = Settings(
        secret_key=secret_key,
        admin_password=admin_password,
        database_url=f"sqlite:///{db_path}",
        data_dir=tmp / "data",
        config_dir=tmp / "config",
        disk_min_free_gb=0,
    )
    run_migrations(settings.database_url)
    subprocess.run(["npm", "run", "build"], cwd=ROOT / "frontend", check=True)
    app = create_app(settings)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    backend_port = int(os.environ.get("MEDIATRACKER_CAPTURE_PORT", "8101"))

    def _serve() -> None:
        uvicorn.run(app, host="127.0.0.1", port=backend_port, log_level="warning")

    threading.Thread(target=_serve, daemon=True).start()
    time.sleep(3)
    factory = app.state.session_factory
    _seed_metro(factory)
    time.sleep(0.6)
    base = f"http://127.0.0.1:{backend_port}"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        def login(page, width: int):
            page.set_viewport_size({"width": width, "height": 1000})
            page.goto(f"{base}/login", wait_until="networkidle")
            page.get_by_label("Nom d'utilisateur").fill("admin")
            page.get_by_label("Mot de passe").fill(admin_password)
            page.get_by_role("button", name="Se connecter").click()
            page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
            page.goto(f"{base}/sante", wait_until="domcontentloaded")
            page.get_by_test_id("health-tab-activite").click()
            page.wait_for_selector("[data-testid='health-activity']", timeout=15000)
            page.wait_for_timeout(1200)

        page = browser.new_page()
        login(page, 1440)
        page.get_by_test_id("metro-level-1").click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(ARTIFACTS / "metro-niveau1-1440.png"), full_page=True)
        def open_metro(page, path_query: str):
            page.goto(f"{base}/sante{path_query}", wait_until="domcontentloaded")
            page.get_by_test_id("health-tab-activite").click()
            page.wait_for_selector("[data-testid='health-activity']", timeout=15000)
            page.wait_for_timeout(600)

        open_metro(page, "?niveau=2")
        page.wait_for_selector("[data-testid='metro-station-panel']", timeout=15000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(ARTIFACTS / "metro-niveau2-1440.png"), full_page=True)
        open_metro(page, "?niveau=2&edge=C06")
        page.wait_for_selector("[data-testid='metro-edge-panel']", timeout=15000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(ARTIFACTS / "metro-correspondance-1440.png"), full_page=True)
        open_metro(page, "?niveau=2&station=DL1")
        page.wait_for_selector("[data-testid='metro-station-panel']", timeout=15000)
        page.wait_for_timeout(300)
        page.screenshot(path=str(ARTIFACTS / "metro-station-paneau-1440.png"), full_page=True)
        open_metro(page, "?niveau=3")
        page.get_by_test_id("metro-level-3").click()
        page.wait_for_timeout(600)
        page.screenshot(path=str(ARTIFACTS / "metro-niveau3-1440.png"), full_page=True)

        mobile = browser.new_page()
        login(mobile, 390)
        mobile.screenshot(path=str(ARTIFACTS / "metro-accordeons-390.png"), full_page=True)
        mobile.goto(f"{base}/sante/station/RNG5", wait_until="domcontentloaded")
        mobile.wait_for_timeout(1000)
        mobile.screenshot(path=str(ARTIFACTS / "metro-station-390.png"), full_page=True)
        browser.close()


if __name__ == "__main__":
    main()
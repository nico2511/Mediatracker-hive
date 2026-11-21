#!/usr/bin/env python3
"""Captures Accueil « À trier » (390 et 1440 px) avec données fictives."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from mediatracker.api.app import create_app  # noqa: E402
from mediatracker.config import Settings  # noqa: E402
from mediatracker.db.migrate import run_migrations  # noqa: E402
from mediatracker.db.models import LibraryFile, Media, MediaItem, MediaSeason, Setting  # noqa: E402
from mediatracker.events import record_event  # noqa: E402
from mediatracker.integrations.tmdb import TmdbSeason, TmdbTitle  # noqa: E402

OUT = ROOT / "captures-a-trier"
ARTIFACTS = Path("/opt/cursor/artifacts")
NOW = datetime.now(timezone.utc)
POSTER = "/icon-192.png"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Variable d'environnement requise : {name}")
    return value


def _seed(db) -> None:
    renewed = Media(
        type="series",
        title="Série démo E5",
        year=2090,
        tmdb_id=901005,
        followed=False,
        poster_url=POSTER,
    )
    disk_a = Media(
        type="series",
        title="Série démo E7",
        year=2091,
        tmdb_id=901007,
        followed=True,
        poster_url=POSTER,
    )
    disk_b = Media(
        type="series",
        title="Série démo E9",
        year=2092,
        tmdb_id=901009,
        followed=True,
        poster_url=POSTER,
    )
    stop = Media(
        type="series",
        title="Série démo E11",
        year=2093,
        tmdb_id=901011,
        followed=True,
        poster_url=POSTER,
    )
    for row in (renewed, disk_a, disk_b, stop):
        db.add(row)
    db.flush()
    db.add(MediaSeason(media_id=renewed.id, number=1, air_date=date(2024, 1, 1), episode_count=8))
    record_event(db, "media.unfollowed", media_id=renewed.id, when=NOW, payload={"title": renewed.title})
    from mediatracker.core.seasons import sync_unfollowed_series_seasons

    fetched = TmdbTitle(
        renewed.tmdb_id,
        "series",
        renewed.title,
        renewed.year,
        None,
        POSTER,
        None,
        None,
        (
            TmdbSeason(1, "Saison 1", None, date(2024, 1, 1), 8, 1, ()),
            TmdbSeason(3, "Saison 3", None, date(2027, 3, 12), 10, 3, ()),
        ),
    )
    sync_unfollowed_series_seasons(db, renewed, fetched, NOW)

    stale = NOW - timedelta(days=45)
    for season, episodes in ((2, 2), (4, 2), (5, 2)):
        media = disk_b if season >= 4 else disk_a
        for ep in range(1, episodes + 1):
            path = f"/data/demo-{media.id}-s{season:02d}e{ep:02d}.mkv"
            item = MediaItem(
                media_id=media.id,
                season=season,
                episode=ep,
                title=f"Épisode démo {season}x{ep}",
                air_date=date(2024, season, ep),
                downloaded=True,
                watched=False,
                file_path=path,
            )
            db.add(item)
            db.flush()
            db.add(
                LibraryFile(
                    path=path,
                    media_type="series",
                    status="matched",
                    media_item_id=item.id,
                    seen_at=stale,
                )
            )
    idle = NOW - timedelta(days=220)
    media = stop
    for ep in range(1, 10):
        watched = ep == 1
        item = MediaItem(
            media_id=media.id,
            season=1,
            episode=ep,
            title=f"Épisode démo 1x{ep}",
            air_date=date(2024, 6, ep),
            downloaded=True,
            watched=watched,
            watched_at=idle if watched else None,
            file_path=f"/data/demo-{media.id}-stop-s01e{ep:02d}.mkv",
        )
        db.add(item)
        db.flush()
        db.add(
            LibraryFile(
                path=item.file_path,
                media_type="series",
                status="matched",
                media_item_id=item.id,
                seen_at=stale,
            )
        )
    db.merge(Setting(key="system.setup_completed", value="1", is_secret=False, updated_at=NOW))
    db.merge(
        Setting(key="library.cross_series_repaired", value="1", is_secret=False, updated_at=NOW)
    )
    db.commit()


def main() -> None:
    admin_password = _require_env("MEDIATRACKER_CAPTURE_ADMIN_PASSWORD")
    secret_key = _require_env("MEDIATRACKER_CAPTURE_SECRET_KEY")
    os.environ["SECRET_KEY"] = secret_key
    tmp = ROOT / ".capture-a-trier"
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
    OUT.mkdir(exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    backend_port = int(os.environ.get("MEDIATRACKER_CAPTURE_PORT", "8099"))

    def _serve() -> None:
        uvicorn.run(app, host="127.0.0.1", port=backend_port, log_level="warning")

    backend = threading.Thread(target=_serve, daemon=True)
    backend.start()
    time.sleep(3)
    db = app.state.session_factory()
    try:
        _seed(db)
    finally:
        db.close()
    time.sleep(0.5)
    fe_base = f"http://127.0.0.1:{backend_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width, name in ((390, "telephone-390"), (1440, "ordinateur-1440")):
                page = browser.new_page(viewport={"width": width, "height": 1000})
                page.goto(f"{fe_base}/login", wait_until="networkidle")
                page.get_by_label("Nom d'utilisateur").fill("admin")
                page.get_by_label("Mot de passe").fill(admin_password)
                page.get_by_role("button", name="Se connecter").click()
                page.wait_for_url(lambda url: "/login" not in url, timeout=15000)
                page.goto(f"{fe_base}/", wait_until="domcontentloaded")
                page.wait_for_timeout(4000)
                probe = page.evaluate(
                    """async () => {
                      const res = await fetch('/api/v1/dashboard/to-sort', { credentials: 'include' });
                      const body = await res.json();
                      return { status: res.status, total: body.total, kinds: (body.groups || []).map(g => g.kind) };
                    }"""
                )
                kinds = set(probe.get("kinds") or [])
                if probe.get("status") != 200 or len(kinds) < 3:
                    raise RuntimeError(f"Bloc À trier incomplet: {probe}")
                block = page.locator("#home-to-sort")
                block.scroll_into_view_if_needed(timeout=30000)
                page.wait_for_timeout(1500)
                for target in (OUT / f"a-trier-{name}.png", ARTIFACTS / f"a-trier-{name}.png"):
                    if width == 390:
                        page.evaluate(
                            """() => {
                              const el = document.getElementById('home-to-sort');
                              if (!el) return;
                              const header = document.querySelector('.mobile-top-bar, header.app-header');
                              const offset = header ? header.getBoundingClientRect().height + 8 : 96;
                              const y = el.getBoundingClientRect().top + window.scrollY - offset;
                              window.scrollTo(0, Math.max(0, y));
                            }"""
                        )
                        page.wait_for_timeout(400)
                        block.screenshot(path=str(target))
                    else:
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        page.wait_for_timeout(800)
                        page.screenshot(path=str(target), full_page=True)
            browser.close()
    finally:
        pass


if __name__ == "__main__":
    main()
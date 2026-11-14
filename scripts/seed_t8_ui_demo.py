#!/usr/bin/env python3
"""Données fictives Hive pour captures UI T8 (indisponible, transferts, menus)."""

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
from tests.conftest import csrf, login  # noqa: E402
from tests.test_hive_t6 import NOW, _ingest, _ruche  # noqa: E402

from mediatracker.api.app import create_app  # noqa: E402
from mediatracker.config import Settings  # noqa: E402
from mediatracker.db.models import (
    Download,
    DownloadHiveFetch,
    HiveCatalogue,
    HiveFileFingerprint,
    LibraryFile,
    Media,
    MediaItem,
    Setting,
)
from mediatracker.hive.hive_catalogue import _stable_work_key, refresh_hive_catalogue_store  # noqa: E402
from mediatracker.hive.peer_gossip import mark_origin_unreachable  # noqa: E402

SETUP_KEY = "system.setup_completed"
DEMO_MARKER_NAME = ".t8_ui_demo_marker"
OUT = Path(os.environ.get("T8_UI_DEMO_DIR", f"/tmp/t8-ui-demo-{os.getpid()}"))
RUCHE_COUNT = max(2, int(os.environ.get("T8_UI_RUCHE_COUNT", "2")))


def _path_is_forbidden_demo_target(path: Path) -> bool:
    resolved = path.expanduser().resolve()
    allowed_tmp = (Path("/tmp").resolve(), Path("/var/tmp").resolve(), Path("/opt/cursor/artifacts").resolve())
    for root in allowed_tmp:
        try:
            if resolved == root or root in resolved.parents:
                return False
        except OSError:
            continue
    home = Path.home().resolve()
    try:
        if resolved == home or home in resolved.parents:
            return True
    except OSError:
        return True
    blocked = (
        Path("/etc"),
        Path("/var/lib"),
        Path("/usr"),
        Path("/agent/repos/MediaTracker").resolve(),
    )
    for root in blocked:
        try:
            if resolved == root or root in resolved.parents:
                return True
        except OSError:
            return True
    return False


def _reset_demo_dir() -> None:
    """Réinitialise uniquement un répertoire démo marqué (jamais la DB utilisateur)."""
    if _path_is_forbidden_demo_target(OUT):
        raise RuntimeError(f"Chemin démo interdit (hors sandbox temporaire) : {OUT}")
    marker = OUT / DEMO_MARKER_NAME
    if OUT.exists():
        if not marker.is_file():
            residual = [p for p in OUT.iterdir()]
            if residual:
                raise RuntimeError(
                    f"Répertoire existant non marqué ({OUT}) — refus avant toute modification.",
                )
    else:
        OUT.mkdir(parents=True, exist_ok=True)
    if not marker.is_file():
        marker.write_text("t8-ui-demo\n", encoding="utf-8")
        return
    db = OUT / "app.db"
    if db.is_file():
        db.unlink()
    data = OUT / "data"
    if data.is_dir():
        shutil.rmtree(data, ignore_errors=True)
    cfg = OUT / "config"
    if cfg.is_dir():
        shutil.rmtree(cfg, ignore_errors=True)


def main() -> None:
    _reset_demo_dir()
    settings = Settings(
        secret_key="t8-demo-secret-key-012345678901",
        admin_username="admin",
        admin_password="test-admin-password",
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
                    "hive_public_address": "https://mtr.exemple.fr",
                }
            },
            headers=csrf(client),
        )
        rid_a = _ruche(client)
        rid_b = _ruche(client)
        for _ in range(RUCHE_COUNT - 2):
            _ruche(client)
        db = app.state.session_factory()
        try:
            media_ok = Media(type="series", title="Série démo fictive", year=2099, tmdb_id=88101)
            media_wait = Media(type="film", title="Film démo indisponible", year=2099, tmdb_id=88102)
            media_share = Media(type="film", title="Film démo partage", year=2020, tmdb_id=88103)
            db.add(media_ok)
            db.add(media_wait)
            db.add(media_share)
            db.flush()
            ep_dir = OUT / "data" / "library" / "series"
            ep_dir.mkdir(parents=True, exist_ok=True)
            ep_file = ep_dir / "ep-demo.mkv"
            ep_file.write_bytes(b"DEMO-T8-" + b"x" * 512)
            item_ok = MediaItem(
                media_id=media_ok.id,
                season=1,
                episode=1,
                title="Ep démo",
                state="downloaded",
                downloaded=True,
                file_path=str(ep_file.resolve()),
            )
            item_wait = MediaItem(media_id=media_wait.id, state="missing")
            film_dir = OUT / "data" / "library" / "movies"
            film_dir.mkdir(parents=True, exist_ok=True)
            film_file = film_dir / "film-demo-partage.mkv"
            film_file.write_bytes(b"DEMO-FILM-T8")
            item_share = MediaItem(
                media_id=media_share.id,
                state="downloaded",
                downloaded=True,
                file_path=str(film_file.resolve()),
            )
            db.add(item_ok)
            db.add(item_wait)
            db.add(item_share)
            db.flush()
            db.add(
                LibraryFile(
                    path=str(film_file.resolve()),
                    size=film_file.stat().st_size,
                    parsed_title="Film démo partage",
                    parsed_year=2020,
                    season=None,
                    episode=None,
                    tmdb_id=88103,
                    media_type="film",
                    media_id=media_share.id,
                    media_item_id=item_share.id,
                    status="matched",
                    confidence="high",
                    seen_at=NOW,
                )
            )
            db.commit()
            db.add(
                HiveFileFingerprint(
                    media_item_id=item_share.id,
                    path=str(film_file.resolve()),
                    size=film_file.stat().st_size,
                    mtime_ns=film_file.stat().st_mtime_ns,
                    sha256_hex="e" * 64,
                    partial_size=film_file.stat().st_size,
                    updated_at=NOW,
                )
            )
            db.add(
                HiveFileFingerprint(
                    media_item_id=item_ok.id,
                    path=str(ep_file.resolve()),
                    size=ep_file.stat().st_size,
                    mtime_ns=ep_file.stat().st_mtime_ns,
                    sha256_hex="d" * 64,
                    partial_size=ep_file.stat().st_size,
                    updated_at=NOW,
                )
            )
            db.commit()
            sha_ok = "a" * 64
            sha_wait = "b" * 64
            sha_alt = "c" * 64
            _ingest(
                db,
                settings,
                ruche_id=rid_a,
                tmdb_id=88101,
                season=1,
                episode=1,
                version="HD+VFF",
                media_type="series",
                sha=sha_ok,
                joignable=True,
                seq=1,
                size=1200,
            )
            _ingest(
                db,
                settings,
                ruche_id=rid_a,
                tmdb_id=88101,
                season=1,
                episode=1,
                version="1080p+VFF",
                media_type="series",
                sha=sha_alt,
                joignable=True,
                seq=2,
                size=2200,
            )
            _ingest(
                db,
                settings,
                ruche_id=rid_b,
                tmdb_id=88102,
                season=None,
                episode=None,
                version="1080p+VFF",
                media_type="film",
                sha=sha_wait,
                joignable=True,
                seq=1,
                size=2400,
            )
            from mediatracker.hive.identity import load_private_key
            from mediatracker.hive.crypto_id import hive_public_id_from_public_key

            pk = load_private_key(settings)
            origin = hive_public_id_from_public_key(pk.public_key())
            mark_origin_unreachable(db, ruche_id=rid_b, origin_id=origin, now=NOW)
            db.commit()
            refresh_hive_catalogue_store(db, now=NOW)
            db.commit()
            dl_demo = Download(
                media_item_id=item_ok.id,
                state="downloading",
                done_bytes=600,
                progress=50,
                created_at=NOW,
            )
            db.add(dl_demo)
            db.flush()
            db.add(
                DownloadHiveFetch(
                    download_id=dl_demo.id,
                    sha256_hex=sha_ok,
                    sources_json=json.dumps([]),
                    ruche_id=rid_a,
                    file_id=b"\x01" * 16,
                    origin_instance_id="hv1demo",
                    peer_base_url="https://mtr.exemple.fr",
                    expected_size=1200,
                    updated_at=NOW,
                )
            )
            db.commit()
            cle_film = {"type": "film", "tmdb_id": 88102, "version": "1080p+VFF"}
            cle_series = {"type": "series", "tmdb_id": 88101, "saison": 1, "episode": 1, "version": "HD+VFF"}
            n = db.query(HiveCatalogue).count()
            (OUT / "seed-meta.json").write_text(
                json.dumps(
                    {
                        "catalogue_rows": n,
                        "ruches": RUCHE_COUNT,
                        "hive_download_id": dl_demo.id,
                        "work_key_film": _stable_work_key(cle_film),
                        "work_key_series": _stable_work_key(
                            {"type": "series", "tmdb_id": 88101, "saison": 1, "episode": 1}
                        ),
                        "media_id_film": media_wait.id,
                        "media_id_series": media_ok.id,
                        "media_id_share_film": media_share.id,
                        "media_item_id_series": item_ok.id,
                        "media_item_id_share_film": item_share.id,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        finally:
            db.close()
    print(f"seed ok: {OUT}")


if __name__ == "__main__":
    main()

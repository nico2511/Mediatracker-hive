"""Réglages audit nov. 2026 : branchements send/recv Hive, suivi, cleanup, migration."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from mediatracker.core.cleanup import apply_auto, auto_delete_globally_enabled
from mediatracker.core.follow import FollowInput, default_pref_language, default_pref_quality, follow
from mediatracker.db.models import Media, Setting
from mediatracker.hive.download_pipeline import hive_aria2_options, sync_hive_recv_rate_limits
from mediatracker.hive.transfer_runtime import apply_hive_send_bandwidth

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def test_migration_0069_drops_obsolete_settings(tmp_path):
    from alembic import command
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from mediatracker.db.migrate import make_config, run_migrations

    url = f"sqlite:///{tmp_path / 'm0069.db'}"
    run_migrations(url)
    engine = create_engine(url)
    keys = ("hive_transfer_notify", "flaresolverr_url", "hive_download_via_fetch_grant")
    with Session(engine) as session:
        for key in keys:
            session.merge(Setting(key=key, value="x", is_secret=False, updated_at=NOW))
        session.commit()
    cfg = make_config(url)
    command.downgrade(cfg, "0068_hive_join_pending_rejected")
    command.upgrade(cfg, "head")
    with Session(engine) as session:
        for key in keys:
            assert session.get(Setting, key) is None


def test_hive_send_bps_applied_on_patch(client, app):
    from tests.conftest import csrf, login

    login(client)
    headers = csrf(client)
    client.patch(
        "/api/v1/settings",
        json={"values": {"hive_transfer_send_bps": 50_000}},
        headers=headers,
    )
    assert app.state.hive_send_bandwidth.bps == 50_000.0


def test_hive_send_bps_applied_at_startup(client, app):
    db = app.state.session_factory()
    try:
        db.merge(Setting(key="hive_transfer_send_bps", value="12000", is_secret=False, updated_at=NOW))
        db.commit()
        apply_hive_send_bandwidth(app, db)
        assert app.state.hive_send_bandwidth.bps == 12_000.0
    finally:
        db.close()


def test_hive_recv_aria2_options(client, app):
    db = app.state.session_factory()
    try:
        db.merge(Setting(key="hive_transfer_recv_bps", value="4096", is_secret=False, updated_at=NOW))
        db.commit()
        opts = hive_aria2_options(db)
        assert opts["max-download-limit"] == "4096"
    finally:
        db.close()


def test_sync_hive_recv_rate_limits_calls_change_option(client, app, settings):
    downloader = MagicMock()
    db = app.state.session_factory()
    try:
        from mediatracker.db.models import Download, DownloadHiveFetch, MediaItem

        row = Media(type="film", title="Lim", year=2020, tmdb_id=1, followed=True)
        db.add(row)
        db.flush()
        item = MediaItem(media_id=row.id, state="missing")
        db.add(item)
        db.flush()
        dl = Download(media_item_id=item.id, state="downloading", handle="gid-test")
        db.add(dl)
        db.flush()
        db.add(
            DownloadHiveFetch(
                download_id=dl.id,
                sha256_hex="a" * 64,
                sources_json="[]",
                ruche_id=b"\x01" * 16,
                file_id=b"\x02" * 16,
                origin_instance_id="peer-a",
                peer_base_url="https://peer.exemple.fr",
                expected_size=1024,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        db.merge(Setting(key="hive_transfer_recv_bps", value="8000", is_secret=False, updated_at=NOW))
        db.commit()
        count = sync_hive_recv_rate_limits(db, downloader)
        assert count == 1
        downloader.change_option.assert_called_once_with("gid-test", {"max-download-limit": "8000"})
    finally:
        db.close()


def test_follow_default_quality_language_from_settings(client, app, settings):
    db = app.state.session_factory()
    try:
        db.merge(Setting(key="default_quality", value="1080p", is_secret=False, updated_at=NOW))
        db.merge(Setting(key="default_language", value="MULTI", is_secret=False, updated_at=NOW))
        db.commit()
        assert default_pref_quality(db) == "1080p"
        assert default_pref_language(db) == "MULTI"
        created, _ = follow(
            db,
            settings.secret_key,
            FollowInput(type="film", title="Démo titre", year=2020),
            source="create",
        )
        assert created.pref_quality == "1080p"
        assert created.pref_language == "MULTI"
    finally:
        db.close()


def test_auto_delete_enabled_api_default_when_unset(client, app):
    from tests.conftest import login

    login(client)
    db = app.state.session_factory()
    try:
        assert db.get(Setting, "auto_delete_enabled") is None
    finally:
        db.close()
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    row = next(item for item in response.json()["settings"] if item["key"] == "auto_delete_enabled")
    assert row["value"] is True
    assert row["set"] is False


def test_auto_delete_disabled_blocks_worker_cleanup(client, app, settings):
    db = app.state.session_factory()
    try:
        assert auto_delete_globally_enabled(db) is True
        db.merge(Setting(key="auto_delete_enabled", value="false", is_secret=False, updated_at=NOW))
        db.commit()
        assert auto_delete_globally_enabled(db) is False
        assert apply_auto(db, settings, NOW) == 0
    finally:
        db.close()

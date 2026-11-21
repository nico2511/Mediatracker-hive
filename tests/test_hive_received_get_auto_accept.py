"""GET /requests/received : rattrapage auto-acceptation des demandes en stock."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy import func, select
from tests.conftest import login, write_sparse_video_file
from tests.test_hive_request_backlog import _add_remote_cherche
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id
from tests.test_hive_t6 import NOW

from mediatracker.db.models import HiveRequestResolution, Media, MediaItem, Setting
from mediatracker.hive.signal_policy import HIVE_AUTO_ACCEPT_REQUESTS_KEY


def _seed_pending_match(client, settings, tmp_path, *, tmdb_id: int, remote: str):
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        cle = {"type": "movie", "tmdb_id": tmdb_id, "version": "1080p+fr"}
        _add_remote_cherche(db, settings, ruche_id=rid, origin_remote=remote, cle=cle)
        media = Media(type="film", title="Œuvre locale test", year=2098, tmdb_id=tmdb_id, followed=False)
        db.add(media)
        db.flush()
        fpath = tmp_path / f"local{tmdb_id}.mkv"
        write_sparse_video_file(fpath, 128_000)
        db.add(
            MediaItem(
                media_id=media.id,
                downloaded=True,
                file_path=str(fpath),
                release_quality="1080p",
                release_language="fr",
            )
        )
        db.flush()
        db.commit()
        return rid, remote
    finally:
        db.close()


def test_get_received_auto_accepts_when_setting_on(client, settings, tmp_path):
    _enable_hive(client)
    login(client)
    remote = "peerGetAuto880100"
    _seed_pending_match(client, settings, tmp_path, tmdb_id=880100, remote=remote)
    db = client.app.state.session_factory()
    try:
        db.merge(Setting(key=HIVE_AUTO_ACCEPT_REQUESTS_KEY, value="true", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/hive/requests/received")
    assert resp.status_code == 200, resp.text
    db = client.app.state.session_factory()
    try:
        row = db.scalar(
            select(HiveRequestResolution).where(
                HiveRequestResolution.requester_origin == remote,
                HiveRequestResolution.status == "accepted",
            )
        )
        assert row is not None
    finally:
        db.close()


def test_get_received_skips_auto_accept_when_setting_off(client, settings, tmp_path):
    _enable_hive(client)
    login(client)
    remote = "peerGetAuto880101"
    _seed_pending_match(client, settings, tmp_path, tmdb_id=880101, remote=remote)
    db = client.app.state.session_factory()
    try:
        db.merge(Setting(key=HIVE_AUTO_ACCEPT_REQUESTS_KEY, value="false", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/v1/hive/requests/received")
    assert resp.status_code == 200, resp.text
    db = client.app.state.session_factory()
    try:
        count = db.scalar(select(func.count()).select_from(HiveRequestResolution))
        assert count == 0
    finally:
        db.close()


def test_get_received_auto_accept_idempotent(client, settings, tmp_path):
    _enable_hive(client)
    login(client)
    remote = "peerGetAuto880102"
    _seed_pending_match(client, settings, tmp_path, tmdb_id=880102, remote=remote)
    db = client.app.state.session_factory()
    try:
        db.merge(Setting(key=HIVE_AUTO_ACCEPT_REQUESTS_KEY, value="true", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    assert client.get("/api/v1/hive/requests/received").status_code == 200
    assert client.get("/api/v1/hive/requests/received").status_code == 200
    db = client.app.state.session_factory()
    try:
        count = db.scalar(
            select(func.count())
            .select_from(HiveRequestResolution)
            .where(HiveRequestResolution.requester_origin == remote)
        )
        assert count == 1
    finally:
        db.close()


def test_get_received_swallows_auto_accept_error(client, settings, tmp_path):
    _enable_hive(client)
    login(client)
    _seed_pending_match(client, settings, tmp_path, tmdb_id=880103, remote="peerGetAuto880103")
    db = client.app.state.session_factory()
    try:
        db.merge(Setting(key=HIVE_AUTO_ACCEPT_REQUESTS_KEY, value="true", is_secret=False, updated_at=NOW))
        db.commit()
    finally:
        db.close()

    with patch(
        "mediatracker.hive.signal_requests.maybe_try_auto_accept_stored_incoming",
        side_effect=RuntimeError("simulated catchup failure"),
    ):
        resp = client.get("/api/v1/hive/requests/received")
    assert resp.status_code == 200, resp.text
    assert "items" in resp.json()

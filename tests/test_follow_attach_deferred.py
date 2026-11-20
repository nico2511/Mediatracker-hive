"""Follow routes commit hive signals before deferred library attach."""

from __future__ import annotations

import json
import threading
from datetime import date
from unittest.mock import patch

from sqlalchemy import func, select
from tests.conftest import csrf, login
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.core.attach_deferred import join_pending, reset_for_tests
from mediatracker.core.worker_wake import REASON_API_MEDIA, WAKE_COUNTS_KEY, WAKE_SEQ_KEY
from mediatracker.db.models import HiveSignal, Media, MediaItem, Setting


def test_create_media_commits_wake_when_attach_deferred(client, settings):
    """follow + wake_worker survive HTTP response while attach is only scheduled."""
    login(client)
    reset_for_tests()
    with patch("mediatracker.core.attach_deferred.schedule_attach_known_files") as schedule:
        created = client.post(
            "/api/v1/media",
            json={"type": "film", "title": "Œuvre test A", "year": 2020, "tmdb_id": 770001},
            headers=csrf(client),
        )
    assert created.status_code == 201, created.text
    assert created.json()["attached_count"] == 0
    schedule.assert_called_once()
    db = client.app.state.session_factory()
    try:
        media = db.get(Media, created.json()["id"])
        assert media is not None and media.followed is True
        counts_row = db.get(Setting, WAKE_COUNTS_KEY)
        assert counts_row is not None
        counts = json.loads(counts_row.value)
        assert counts.get(REASON_API_MEDIA, 0) >= 1
        seq_row = db.get(Setting, WAKE_SEQ_KEY)
        assert seq_row is not None and int(seq_row.value) >= 1
    finally:
        db.close()


def test_create_media_does_not_attach_on_request_thread(client, settings):
    login(client)
    reset_for_tests()
    calls: list[str] = []

    def fake_schedule(factory, cfg, media_id: int) -> None:
        calls.append("schedule")

    with (
        patch(
            "mediatracker.core.attach_deferred.schedule_attach_known_files",
            side_effect=fake_schedule,
        ),
        patch(
            "mediatracker.library.scanner._jellyfin_folder_ids",
            side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("jellyfin during request")),
        ),
        patch(
            "mediatracker.core.library_plan.list_folders",
            side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("rglob during request")),
        ),
    ):
        created = client.post(
            "/api/v1/media",
            json={"type": "film", "title": "Œuvre test B", "year": 2020, "tmdb_id": 770002},
            headers=csrf(client),
        )
    assert created.status_code == 201, created.text
    assert calls == ["schedule"]


def test_deferred_attach_runs_off_request_thread(client, settings):
    login(client)
    reset_for_tests()
    started = threading.Event()

    with patch("mediatracker.core.attach_deferred._run", side_effect=lambda *a, **k: started.set()):
        created = client.post(
            "/api/v1/media",
            json={"type": "film", "title": "Œuvre test C", "year": 2020, "tmdb_id": 770003},
            headers=csrf(client),
        )
    assert created.status_code == 201, created.text
    assert started.wait(timeout=5)
    join_pending()


def test_follow_existing_commits_cherche_without_attach(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    media_id = 0
    past_air = date(2020, 6, 1)
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media = Media(type="series", title="Œuvre test D", year=2020, tmdb_id=770004, followed=False)
        db.add(media)
        db.flush()
        media_id = media.id
        db.add(
            MediaItem(
                media_id=media.id,
                season=1,
                episode=1,
                air_date=past_air,
            )
        )
        db.commit()
    finally:
        db.close()
    reset_for_tests()
    login(client)
    with patch("mediatracker.core.attach_deferred.schedule_attach_known_files") as schedule:
        followed = client.post(
            f"/api/v1/media/{media_id}/follow",
            json={},
            headers=csrf(client),
        )
    assert followed.status_code == 200, followed.text
    schedule.assert_not_called()
    db = client.app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) >= 1
    finally:
        db.close()
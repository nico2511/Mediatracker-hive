"""LOT4 : Hive source standard via fetch-grant + aria2."""

from __future__ import annotations

from datetime import datetime, timezone

from mediatracker.core.profile_attempt_orchestration import download_source_order
from mediatracker.db.models import Media, Setting
from mediatracker.hive.download_pipeline import hive_aria2_options

pytest_plugins = ["tests.test_hive_t5"]


def test_lot4_hive_aria2_includes_recv_limit(client, app):
    from datetime import datetime, timezone

    from mediatracker.db.models import Setting

    moment = datetime.now(timezone.utc)
    db = app.state.session_factory()
    try:
        db.merge(Setting(key="hive_transfer_recv_bps", value="1024", is_secret=False, updated_at=moment))
        db.commit()
        assert hive_aria2_options(db)["max-download-limit"] == "1024"
    finally:
        db.close()


def test_lot4_followed_hive_download_via_aria2(transfer_pair):
    from tests.test_hive_fetch_grant_lot2 import test_lot2_enqueue_creates_download_not_transfer_job

    test_lot2_enqueue_creates_download_not_transfer_job(transfer_pair)


def test_lot4_source_order_not_forcing_hive_first(client, app):
    db = app.state.session_factory()
    settings = app.state.settings
    try:
        moment = datetime.now(timezone.utc)
        db.merge(
            Setting(
                key="download_source_order",
                value="wawacity,hive,comet,torrentio",
                is_secret=False,
                updated_at=moment,
            )
        )
        db.merge(Setting(key="hive_enabled", value="true", is_secret=False, updated_at=moment))
        media = Media(type="film", title="Ordre", year=2020, tmdb_id=9102, followed=True)
        db.add(media)
        db.commit()
        order = download_source_order(db, media, settings)
        assert "hive" in order
        if "comet" in order:
            assert order.index("comet") < order.index("hive")
    finally:
        db.close()

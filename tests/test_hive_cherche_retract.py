"""Retrait mémorisé : pas de republication cherche automatique."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.db.models import HiveChercheRetract, HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import _cle_document
from mediatracker.hive.signal_cherche_retract import cherche_retract_active
from mediatracker.hive.signal_miss_prompt import cherche_miss_prompt_active
from mediatracker.hive.signal_policy import (
    HIVE_REQUEST_ON_MISS_KEY,
    explicit_announce_cherche,
    maybe_publish_cherche_on_follow,
    maybe_publish_cherche_on_source_miss,
    retract_cherche_follow_item,
)
from mediatracker.hive.signal_requests import list_my_requests, local_cherche_active


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _followed_movie(db, title: str, tmdb_id: int) -> tuple[Media, MediaItem]:
    media = Media(type="movie", title=title, year=2099, tmdb_id=tmdb_id, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id)
    db.add(item)
    db.flush()
    return media, item


def test_retract_blocks_auto_republish_with_alldebrid(client, settings):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value="auto"))
        media, item = _followed_movie(db, "Œuvre retract A", 880101)
        moment = _utc_now()
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=moment)
        db.flush()
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        retract_cherche_follow_item(
            db, settings, media=media, item=item, now=moment, remember_user_withdraw=True
        )
        db.flush()
        assert cherche_retract_active(db, int(item.id))
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=moment)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        assert not cherche_miss_prompt_active(db, int(item.id))
        rows = list_my_requests(db, settings)
        assert any(r["status"] == "retracted" for r in rows)
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.commit()
    finally:
        db.close()


def test_retract_blocks_follow_without_alldebrid(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _followed_movie(db, "Œuvre retract B", 880102)
        moment = _utc_now()
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=moment)
        db.flush()
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        retract_cherche_follow_item(
            db, settings, media=media, item=item, now=moment, remember_user_withdraw=True
        )
        db.flush()
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=moment)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        db.commit()
    finally:
        db.close()


def test_explicit_announce_clears_retract_and_republishes(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _followed_movie(db, "Œuvre retract C", 880103)
        moment = _utc_now()
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=moment)
        db.flush()
        retract_cherche_follow_item(
            db, settings, media=media, item=item, now=moment, remember_user_withdraw=True
        )
        db.flush()
        assert cherche_retract_active(db, int(item.id))
        assert explicit_announce_cherche(db, settings, media=media, item=item, now=moment)
        db.flush()
        assert not cherche_retract_active(db, int(item.id))
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        rows = list_my_requests(db, settings)
        assert any(r["status"] in {"pending", "transfer"} for r in rows)
        db.commit()
    finally:
        db.close()


def test_retract_row_persisted(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media, item = _followed_movie(db, "Œuvre retract D", 880104)
        moment = _utc_now()
        retract_cherche_follow_item(
            db, settings, media=media, item=item, now=moment, remember_user_withdraw=True
        )
        db.flush()
        assert db.get(HiveChercheRetract, item.id) is not None
        db.commit()
    finally:
        db.close()
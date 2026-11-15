"""Suivi → signal cherche Hive sur tous les chemins (sans clé AllDebrid)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.core.follow import FollowInput, follow
from mediatracker.db.models import HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import _cle_document
from mediatracker.hive.signal_policy import (
    CHERCHE_CATCHUP_AT_KEY,
    maybe_catchup_cherche_for_followed,
)
from mediatracker.hive.signal_requests import local_cherche_active


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _plateau_film(db, *, tmdb_id: int, title: str) -> tuple[Media, MediaItem]:
    media = Media(type="film", title=title, year=2099, tmdb_id=tmdb_id, followed=False)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None)
    db.add(item)
    db.flush()
    return media, item


def _plateau_series_episodes(
    db,
    media: Media,
    specs: list[tuple[int, int, bool, date | None]],
) -> list[MediaItem]:
    out: list[MediaItem] = []
    for season, episode, watched, air_date in specs:
        row = MediaItem(
            media_id=media.id,
            season=season,
            episode=episode,
            watched=watched,
            air_date=air_date,
        )
        db.add(row)
        db.flush()
        out.append(row)
    return out


PAST_AIR = date(2020, 6, 1)
FUTURE_AIR = date(2099, 12, 31)


def test_follow_existing_publishes_cherche_without_alldebrid(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media, item = _plateau_film(db, tmdb_id=881001, title="Œuvre suivi A")
        db.commit()
        follow(
            db,
            None,
            FollowInput(type="film", title=media.title),
            media=media,
            source="existing",
            settings=settings,
        )
        db.flush()
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 1
        db.commit()
    finally:
        db.close()


def test_follow_existing_skips_cherche_with_alldebrid(client, settings):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media, _item = _plateau_film(db, tmdb_id=881002, title="Œuvre suivi B")
        db.commit()
        follow(
            db,
            None,
            FollowInput(type="film", title=media.title),
            media=media,
            source="existing",
            settings=settings,
        )
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.commit()
    finally:
        db.close()


def test_follow_skips_when_file_present_or_watched(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        fpath = tmp_path / "owned.mkv"
        fpath.write_bytes(b"\x01" * 32)
        media, item = _plateau_film(db, tmdb_id=881003, title="Œuvre suivi C")
        item.downloaded = True
        item.file_path = str(fpath)
        db.commit()
        follow(
            db,
            None,
            FollowInput(type="film", title=media.title),
            media=media,
            source="existing",
            settings=settings,
        )
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        fpath.unlink()
        item.downloaded = False
        item.file_path = None
        item.watched = True
        media.followed = False
        db.flush()
        follow(
            db,
            None,
            FollowInput(type="film", title=media.title),
            media=media,
            source="existing",
            settings=settings,
        )
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.commit()
    finally:
        db.close()


def test_series_follow_publishes_only_missing_episodes(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media = Media(type="series", title="Œuvre suivi D", year=2099, tmdb_id=881004, followed=False)
        db.add(media)
        db.flush()
        ep1, ep2 = _plateau_series_episodes(
            db,
            media,
            [(1, 1, True, PAST_AIR), (1, 2, False, PAST_AIR)],
        )
        db.commit()
        follow(
            db,
            None,
            FollowInput(type="series", title=media.title),
            media=media,
            source="existing",
            settings=settings,
        )
        db.flush()
        cle_watched = _cle_document(media, ep1)
        cle_missing = _cle_document(media, ep2)
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_watched)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_missing)
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 1
        db.commit()
    finally:
        db.close()


def test_catchup_publishes_once_then_skips(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media, item = _plateau_film(db, tmdb_id=881005, title="Œuvre suivi E")
        media.followed = True
        db.flush()
        moment = _utc_now()
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        count_first = db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche"))
        assert count_first == 1
        payload = json.loads(db.scalar(select(HiveSignal.payload_json).limit(1)))
        assert payload["signal"] == "cherche"
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment) is False
        db.flush()
        count_second = db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche"))
        assert count_second == 1
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment) is True
        db.flush()
        assert (
            db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 1
        )
        db.commit()
    finally:
        db.close()


def test_series_future_episode_published_after_air_via_catchup(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media = Media(type="series", title="Œuvre suivi F", year=2099, tmdb_id=881006, followed=False)
        db.add(media)
        db.flush()
        ep = _plateau_series_episodes(db, media, [(1, 3, False, FUTURE_AIR)])[0]
        db.commit()
        moment = datetime(2026, 6, 1, tzinfo=timezone.utc)
        follow(
            db,
            None,
            FollowInput(type="series", title=media.title),
            media=media,
            source="existing",
            settings=settings,
            moment=moment,
        )
        db.flush()
        cle = _cle_document(media, ep)
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 0
        ep.air_date = date(2026, 5, 1)
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        db.commit()
    finally:
        db.close()


def test_follow_skips_cherche_when_on_hold(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media, _item = _plateau_film(db, tmdb_id=881007, title="Œuvre suivi G")
        db.commit()
        follow(
            db,
            None,
            FollowInput(type="film", title=media.title, on_hold=True),
            media=media,
            source="existing",
            settings=settings,
        )
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        media.on_hold = False
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(_utc_now() - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=_utc_now())
        db.flush()
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 1
        db.commit()
    finally:
        db.close()
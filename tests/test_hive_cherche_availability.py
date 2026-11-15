"""Cherche Hive : pas d'annonce pour médias pas encore disponibles."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.core.follow import FollowInput, follow
from mediatracker.db.models import HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import _cle_document
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.signal import _latest_signal_row
from mediatracker.hive.signal_cherche_retract import cherche_retract_active
from mediatracker.hive.signal_miss_prompt import cherche_miss_prompt_active, set_cherche_miss_prompt
from mediatracker.hive.signal_policy import (
    CHERCHE_CATCHUP_AT_KEY,
    HIVE_REQUEST_ON_MISS_KEY,
    maybe_catchup_cherche_for_followed,
    maybe_publish_cherche_on_follow,
    maybe_publish_cherche_on_source_miss,
    publish_cherche_for_item,
    retract_cherche_follow_item,
)
from mediatracker.hive.signal_requests import list_my_requests, local_cherche_active


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


PAST_RELEASE = date(2020, 6, 1)
FUTURE_RELEASE = date(2099, 12, 31)
PAST_AIR = date(2020, 6, 1)
FUTURE_AIR = date(2099, 12, 31)
FIXED_MOMENT = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def _film(db, *, tmdb_id: int, title: str, next_air: date | None, followed: bool = True) -> tuple[Media, MediaItem]:
    media = Media(
        type="film",
        title=title,
        year=2099,
        tmdb_id=tmdb_id,
        followed=followed,
        next_air_date=next_air,
    )
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, season=None, episode=None)
    db.add(item)
    db.flush()
    return media, item


def _series_eps(db, media: Media, specs: list[tuple[int, int, date | None]]) -> list[MediaItem]:
    out: list[MediaItem] = []
    for season, episode, air_date in specs:
        row = MediaItem(media_id=media.id, season=season, episode=episode, air_date=air_date)
        db.add(row)
        db.flush()
        out.append(row)
    return out


def test_future_film_skips_cherche_on_follow(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        media, item = _film(db, tmdb_id=882001, title="Œuvre futur A", next_air=FUTURE_RELEASE, followed=False)
        db.commit()
        follow(
            db,
            None,
            FollowInput(type="film", title=media.title),
            media=media,
            source="existing",
            settings=settings,
            moment=FIXED_MOMENT,
        )
        db.flush()
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 0
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=FIXED_MOMENT)
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.commit()
    finally:
        db.close()


def test_future_film_skips_cherche_on_catchup(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        _film(db, tmdb_id=882002, title="Œuvre futur B", next_air=FUTURE_RELEASE)
        db.commit()
        assert maybe_catchup_cherche_for_followed(db, settings, now=FIXED_MOMENT) is True
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.commit()
    finally:
        db.close()


def test_future_film_skips_cherche_on_source_miss_and_ask_prompt(client, settings):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _join_fixture_ruche(db, _origin_id(settings))
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value="auto"))
        media, item = _film(db, tmdb_id=882003, title="Œuvre futur C", next_air=FUTURE_RELEASE)
        db.commit()
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=FIXED_MOMENT)
        db.flush()
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value="ask"))
        db.flush()
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=FIXED_MOMENT)
        db.flush()
        assert item.id is not None
        assert not cherche_miss_prompt_active(db, item.id)
        db.commit()
    finally:
        db.close()


def test_available_film_publishes_cherche(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _film(db, tmdb_id=882004, title="Œuvre dispo A", next_air=PAST_RELEASE)
        db.commit()
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=FIXED_MOMENT)
        db.flush()
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        db.commit()
    finally:
        db.close()


def test_source_miss_series_publishes_only_aired_episode(client, settings):
    settings = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = settings
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        db.merge(Setting(key=HIVE_REQUEST_ON_MISS_KEY, value="auto"))
        media = Media(type="series", title="Œuvre série mix", year=2099, tmdb_id=882005, followed=True)
        db.add(media)
        db.flush()
        aired, future = _series_eps(
            db, media, [(1, 1, PAST_AIR), (1, 2, FUTURE_AIR)]
        )
        db.commit()
        maybe_publish_cherche_on_source_miss(db, settings, media=media, now=FIXED_MOMENT)
        db.flush()
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=_cle_document(media, aired))
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=_cle_document(media, future))
        assert (
            db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 1
        )
        db.commit()
    finally:
        db.close()


def test_catchup_retracts_cherche_when_film_becomes_unavailable(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _film(db, tmdb_id=882006, title="Œuvre retract futur", next_air=PAST_RELEASE)
        db.commit()
        publish_cherche_for_item(db, settings, media=media, item=item, now=FIXED_MOMENT)
        db.flush()
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        media.next_air_date = FUTURE_RELEASE
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(FIXED_MOMENT - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=FIXED_MOMENT)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=FIXED_MOMENT)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        db.commit()
    finally:
        db.close()


def test_catchup_preserves_voluntary_retract_for_future_film(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _film(db, tmdb_id=882007, title="Œuvre retract volontaire", next_air=PAST_RELEASE)
        db.commit()
        moment = FIXED_MOMENT
        publish_cherche_for_item(db, settings, media=media, item=item, now=moment)
        db.flush()
        retract_cherche_follow_item(
            db, settings, media=media, item=item, now=moment, remember_user_withdraw=True
        )
        db.flush()
        assert item.id is not None
        assert cherche_retract_active(db, item.id)
        media.next_air_date = FUTURE_RELEASE
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        for _ in range(2):
            assert maybe_catchup_cherche_for_followed(db, settings, now=moment)
            db.merge(
                Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat())
            )
            db.flush()
        assert cherche_retract_active(db, item.id)
        media.next_air_date = PAST_RELEASE
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        cle = _cle_document(media, item)
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        assert cherche_retract_active(db, item.id)
        db.commit()
    finally:
        db.close()


def test_catchup_retracts_cherche_once_for_on_hold_media(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _film(db, tmdb_id=882008, title="Œuvre en pause", next_air=PAST_RELEASE)
        db.commit()
        moment = FIXED_MOMENT
        publish_cherche_for_item(db, settings, media=media, item=item, now=moment)
        db.flush()
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        media.on_hold = True
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle)
        assert latest is not None and latest.signal == "retire"
        retire_count = db.scalar(
            select(func.count())
            .select_from(HiveSignal)
            .where(HiveSignal.signal == "retire", HiveSignal.cle_hash == latest.cle_hash)
        )
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert (
            db.scalar(
                select(func.count())
                .select_from(HiveSignal)
                .where(HiveSignal.signal == "retire", HiveSignal.cle_hash == latest.cle_hash)
            )
            == retire_count
        )
        assert db.scalar(select(func.count()).select_from(HiveSignal).where(HiveSignal.signal == "cherche")) == 0
        db.commit()
    finally:
        db.close()


def _with_alldebrid(client, settings):
    updated = settings.model_copy(update={"alldebrid_api_key": "fictive-adb-tst-k"})
    client.app.state.settings = updated
    return updated


def test_alldebrid_catchup_retracts_unaired_episode_once_and_clears_prompt(client, settings):
    settings = _with_alldebrid(client, settings)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media = Media(type="series", title="Œuvre série future D", year=2099, tmdb_id=882009, followed=True)
        db.add(media)
        db.flush()
        future_ep = _series_eps(db, media, [(1, 1, FUTURE_AIR)])[0]
        db.commit()
        moment = FIXED_MOMENT
        publish_cherche_for_item(db, settings, media=media, item=future_ep, now=moment)
        db.flush()
        assert future_ep.id is not None
        set_cherche_miss_prompt(db, future_ep.id, now=moment)
        db.flush()
        cle = _cle_document(media, future_ep)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        assert cherche_miss_prompt_active(db, future_ep.id)
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        assert not cherche_miss_prompt_active(db, future_ep.id)
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle)
        assert latest is not None and latest.signal == "retire"
        retire_count = db.scalar(
            select(func.count())
            .select_from(HiveSignal)
            .where(HiveSignal.signal == "retire", HiveSignal.cle_hash == latest.cle_hash)
        )
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert (
            db.scalar(
                select(func.count())
                .select_from(HiveSignal)
                .where(HiveSignal.signal == "retire", HiveSignal.cle_hash == latest.cle_hash)
            )
            == retire_count
        )
        db.commit()
    finally:
        db.close()


def test_alldebrid_catchup_does_not_publish_for_aired_episode(client, settings):
    settings = _with_alldebrid(client, settings)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media = Media(type="series", title="Œuvre série dispo E", year=2099, tmdb_id=882010, followed=True)
        db.add(media)
        db.flush()
        aired_ep = _series_eps(db, media, [(1, 1, PAST_AIR)])[0]
        db.commit()
        moment = FIXED_MOMENT
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment) is False
        db.flush()
        cle = _cle_document(media, aired_ep)
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        assert db.scalar(select(HiveSignal).where(HiveSignal.signal == "cherche").limit(1)) is None
        db.commit()
    finally:
        db.close()


def test_alldebrid_catchup_preserves_voluntary_retract_for_future_film(client, settings):
    settings = _with_alldebrid(client, settings)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        _join_fixture_ruche(db, origin)
        media, item = _film(db, tmdb_id=882011, title="Œuvre retract AllDebrid", next_air=PAST_RELEASE)
        db.commit()
        moment = FIXED_MOMENT
        publish_cherche_for_item(db, settings, media=media, item=item, now=moment)
        db.flush()
        retract_cherche_follow_item(
            db, settings, media=media, item=item, now=moment, remember_user_withdraw=True
        )
        db.flush()
        assert item.id is not None
        assert cherche_retract_active(db, item.id)
        media.next_air_date = FUTURE_RELEASE
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        db.flush()
        maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert cherche_retract_active(db, item.id)
        db.commit()
    finally:
        db.close()


def test_list_my_requests_hides_ineligible_slots(client, settings):
    settings = _with_alldebrid(client, settings)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        future_film, future_film_item = _film(
            db, tmdb_id=882012, title="Œuvre liste futur", next_air=FUTURE_RELEASE
        )
        on_hold_film, on_hold_item = _film(db, tmdb_id=882013, title="Œuvre liste pause", next_air=PAST_RELEASE)
        on_hold_film.on_hold = True
        aired_film, aired_item = _film(db, tmdb_id=882014, title="Œuvre liste dispo", next_air=PAST_RELEASE)
        series = Media(type="series", title="Œuvre liste série", year=2099, tmdb_id=882015, followed=True)
        db.add(series)
        db.flush()
        future_ep = _series_eps(db, series, [(1, 1, FUTURE_AIR)])[0]
        moment = FIXED_MOMENT
        db.flush()
        for media, item in [
            (future_film, future_film_item),
            (on_hold_film, on_hold_item),
            (aired_film, aired_item),
            (series, future_ep),
        ]:
            publish_cherche_for_item(db, settings, media=media, item=item, now=moment)
            assert item.id is not None
            set_cherche_miss_prompt(db, item.id, now=moment)
        db.commit()
        rows = list_my_requests(db, settings)
        work_keys = {r["work_key"] for r in rows}
        aired_key = _stable_work_key(_cle_document(aired_film, aired_item))
        assert aired_key in work_keys
        assert len(rows) == 1
        assert rows[0]["status"] in {"pending", "transfer"}
        assert local_cherche_active(
            db, ruche_id=rid, origin=origin, cle=_cle_document(aired_film, aired_item)
        )
        db.commit()
    finally:
        db.close()
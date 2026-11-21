"""Prise en charge automatique des recherches Hive (hive_auto_take_wanted)."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from tests.conftest import csrf, login
from tests.test_hive_t2 import _enable_hive
from tests.test_hive_wanted_v3 import NOW, REMOTE, _add_cherche, _film_cle, _patch_tmdb, _utc_now

from mediatracker.db.models import Download, HiveWantedVolunteer, Media, MediaItem, Setting
from mediatracker.hive.signal_policy import HIVE_AUTO_TAKE_WANTED_KEY
from mediatracker.hive.signal_wanted import (
    AUTO_TAKE_WANTED_MAX_PER_PASS,
    maybe_try_auto_take_wanted,
    try_auto_take_wanted_for_instance,
)
from mediatracker.integrations.tmdb import TmdbEpisode, TmdbSeason, TmdbTitle


def _enable_auto_take(db) -> None:
    db.merge(Setting(key=HIVE_AUTO_TAKE_WANTED_KEY, value="true", is_secret=False, updated_at=NOW))
    db.commit()


def test_auto_take_creates_volunteer(client, settings, monkeypatch):
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882001))
        _enable_auto_take(db)
        taken = maybe_try_auto_take_wanted(db, settings, now=_utc_now())
        assert taken == 1
        assert db.scalar(select(func.count()).select_from(HiveWantedVolunteer)) == 1
        media = db.scalar(select(Media).where(Media.tmdb_id == 882001).limit(1))
        assert media is not None and media.followed
    finally:
        db.close()


def test_auto_take_skipped_when_setting_off(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882002))
        taken = maybe_try_auto_take_wanted(db, settings, now=_utc_now())
        assert taken == 0
        assert db.scalar(select(HiveWantedVolunteer).limit(1)) is None
    finally:
        db.close()


def test_auto_take_skips_followed_fiche(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882003))
        db.add(Media(type="film", title="Déjà suivie", year=2099, tmdb_id=882003, followed=True))
        db.flush()
        _enable_auto_take(db)
        taken = maybe_try_auto_take_wanted(db, settings, now=_utc_now())
        assert taken == 0
        assert db.scalar(select(HiveWantedVolunteer).limit(1)) is None
    finally:
        db.close()


def test_auto_take_skips_active_download(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882004))
        media = Media(type="film", title="Téléchargement", year=2099, tmdb_id=882004, followed=False)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id)
        db.add(item)
        db.flush()
        db.add(Download(media_item_id=item.id, state="downloading", progress=12))
        db.flush()
        _enable_auto_take(db)
        taken = maybe_try_auto_take_wanted(db, settings, now=_utc_now())
        assert taken == 0
    finally:
        db.close()


def test_auto_take_respects_max_per_pass(client, settings, monkeypatch):
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        for idx in range(AUTO_TAKE_WANTED_MAX_PER_PASS + 2):
            _add_cherche(db, settings, origin=f"{REMOTE}{idx}", cle=_film_cle(882100 + idx))
        _enable_auto_take(db)
        taken = try_auto_take_wanted_for_instance(db, settings, now=_utc_now())
        assert taken == AUTO_TAKE_WANTED_MAX_PER_PASS
        count = db.scalar(select(func.count()).select_from(HiveWantedVolunteer))
        assert count == AUTO_TAKE_WANTED_MAX_PER_PASS
    finally:
        db.close()


def _patch_tmdb_series(monkeypatch) -> None:
    class FakeTmdb:
        def __init__(self, _token: str, **_kwargs) -> None:
            pass

        def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
            if media_type == "series":
                return TmdbTitle(
                    tmdb_id=tmdb_id,
                    media_type="series",
                    title="Série démo",
                    year=2098,
                    overview="Fiction test.",
                    poster_url=None,
                    series_status="ended",
                    release_date=None,
                    seasons=(
                        TmdbSeason(
                            number=1,
                            name="S1",
                            overview=None,
                            air_date=date(2090, 1, 1),
                            episode_count=2,
                            tmdb_id=None,
                            episodes=(
                                TmdbEpisode(1, 1, "Ep1", date(2090, 1, 1), None),
                                TmdbEpisode(1, 2, "Ep2", date(2090, 1, 2), None),
                            ),
                        ),
                    ),
                )
            return TmdbTitle(
                tmdb_id=tmdb_id,
                media_type=media_type,
                title="La Grange Numérique",
                year=2098,
                overview="Fiction test.",
                poster_url=None,
                series_status=None,
                release_date=None,
                seasons=(),
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok-demo")
    monkeypatch.setattr("mediatracker.hive.wanted_tmdb.TmdbClient", FakeTmdb)

    def _borrow(token: str, _factory, **_) -> tuple[FakeTmdb, bool]:
        return FakeTmdb(token), True

    for path in (
        "mediatracker.integrations.tmdb.borrow_tmdb",
        "mediatracker.core.follow.borrow_tmdb",
    ):
        monkeypatch.setattr(path, _borrow)


def test_auto_take_one_per_coarse_work(client, settings, monkeypatch):
    _patch_tmdb_series(monkeypatch)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        tmdb = 882200
        _add_cherche(
            db,
            settings,
            origin=f"{REMOTE}a",
            cle={"type": "series", "tmdb_id": tmdb, "saison": 1, "episode": 1, "version": "1080p+fr"},
        )
        _add_cherche(
            db,
            settings,
            origin=f"{REMOTE}b",
            cle={"type": "series", "tmdb_id": tmdb, "saison": 1, "episode": 2, "version": "1080p+fr"},
        )
        _enable_auto_take(db)
        taken = try_auto_take_wanted_for_instance(db, settings, now=_utc_now())
        assert taken == 1
        assert db.scalar(select(func.count()).select_from(HiveWantedVolunteer)) == 1
    finally:
        db.close()


def test_settings_patch_triggers_auto_take(client, settings, monkeypatch):
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882005))
        db.commit()
    finally:
        db.close()

    resp = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_auto_take_wanted": True}},
        headers=csrf(client),
    )
    assert resp.status_code == 200, resp.text
    db = client.app.state.session_factory()
    try:
        assert db.scalar(select(HiveWantedVolunteer).limit(1)) is not None
    finally:
        db.close()


def test_disable_setting_does_not_release_volunteer(client, settings, monkeypatch):
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    login(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882006))
        _enable_auto_take(db)
        maybe_try_auto_take_wanted(db, settings, now=_utc_now())
        db.commit()
    finally:
        db.close()

    resp = client.patch(
        "/api/v1/settings",
        json={"values": {"hive_auto_take_wanted": False}},
        headers=csrf(client),
    )
    assert resp.status_code == 200, resp.text
    db = client.app.state.session_factory()
    try:
        assert db.scalar(select(func.count()).select_from(HiveWantedVolunteer)) == 1
    finally:
        db.close()


def test_auto_take_skipped_when_hive_disabled(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(882007))
        _enable_auto_take(db)
        db.merge(Setting(key="hive_enabled", value="false", is_secret=False, updated_at=NOW))
        db.commit()
        taken = maybe_try_auto_take_wanted(db, settings, now=_utc_now())
        assert taken == 0
        assert db.scalar(select(HiveWantedVolunteer).limit(1)) is None
    finally:
        db.close()

"""Hive V3 : onglet Recherché, volontaires, délai possede."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from tests.conftest import login
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.db.models import HiveRequestResolution, HiveSignal, HiveWantedVolunteer, Media, MediaItem
from mediatracker.hive.catalog import sign_payload, sync_hive_catalog_item
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.share_policy import has_share_intent
from mediatracker.hive.signal import build_signal_payload, cle_hash, item_file_present, next_signal_seq
from mediatracker.hive.signal_policy import maybe_publish_cherche_on_follow
from mediatracker.hive.signal_wanted import WANTED_POSSEDE_GRACE_HOURS, list_wanted_requests, take_wanted_request
from mediatracker.hive.wanted_tmdb import clear_wanted_tmdb_cache
from mediatracker.integrations.tmdb import TmdbTitle

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
REMOTE = "peerRecherchePlateau01"


def _utc_now() -> datetime:
    return NOW


def _film_cle(tmdb_id: int = 881001) -> dict:
    return {"type": "movie", "tmdb_id": tmdb_id, "version": "1080p+fr"}


def _add_cherche(db, settings, *, origin: str, cle: dict, created_at: datetime | None = None) -> bytes:
    rid = _join_fixture_ruche(db, _origin_id(settings))
    pk = load_private_key(settings)
    assert pk is not None
    seq = next_signal_seq(db, rid, origin)
    payload = build_signal_payload(ruche_id=rid, origin_instance_id=origin, seq=seq, signal="cherche", cle=cle)
    moment = created_at or _utc_now()
    db.add(
        HiveSignal(
            ruche_id=rid,
            origin_instance_id=origin,
            seq=seq,
            created_at=moment,
            signal="cherche",
            cle_hash=cle_hash(cle),
            cle_json=json.dumps(cle),
            payload_json=json.dumps(payload),
            signature_b64=sign_payload(pk, payload),
        )
    )
    db.flush()
    return rid


def _add_possede(db, settings, *, origin: str, cle: dict) -> None:
    pk = load_private_key(settings)
    assert pk is not None
    origin_self = _origin_id(settings)
    rid = _join_fixture_ruche(db, origin_self)
    seq = next_signal_seq(db, rid, origin)
    payload = build_signal_payload(ruche_id=rid, origin_instance_id=origin, seq=seq, signal="possede", cle=cle)
    db.add(
        HiveSignal(
            ruche_id=rid,
            origin_instance_id=origin,
            seq=seq,
            created_at=_utc_now(),
            signal="possede",
            cle_hash=cle_hash(cle),
            cle_json=json.dumps(cle),
            payload_json=json.dumps(payload),
            signature_b64=sign_payload(pk, payload),
        )
    )
    db.flush()


def test_wanted_visible_without_possede(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle()
        _add_cherche(db, settings, origin=REMOTE, cle=cle)
        rows = list_wanted_requests(db, settings, now=_utc_now())
        assert len(rows) == 1
        assert rows[0]["requester_count"] == 1
        assert rows[0]["volunteering"] is False
    finally:
        db.close()


def test_wanted_hidden_while_possede_within_grace(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881002)
        early = _utc_now() - timedelta(hours=12)
        _add_cherche(db, settings, origin=REMOTE, cle=cle, created_at=early)
        _add_possede(db, settings, origin="peerPossede01", cle=cle)
        assert list_wanted_requests(db, settings, now=_utc_now()) == []
    finally:
        db.close()


def test_wanted_visible_after_grace_without_accept(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881003)
        first = _utc_now() - timedelta(hours=WANTED_POSSEDE_GRACE_HOURS + 1)
        _add_cherche(db, settings, origin=REMOTE, cle=cle, created_at=first)
        _add_possede(db, settings, origin="peerPossede02", cle=cle)
        rows = list_wanted_requests(db, settings, now=_utc_now())
        assert len(rows) == 1
    finally:
        db.close()


def test_wanted_hidden_after_accepted_transfer(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881004)
        rid = _add_cherche(db, settings, origin=REMOTE, cle=cle)
        db.add(
            HiveRequestResolution(
                ruche_id=rid,
                requester_origin=REMOTE,
                cle_hash=cle_hash(cle),
                status="accepted",
                updated_at=_utc_now(),
            )
        )
        db.flush()
        assert list_wanted_requests(db, settings, now=_utc_now()) == []
    finally:
        db.close()


def test_wanted_excludes_local_possession(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881005)
        _add_cherche(db, settings, origin=REMOTE, cle=cle)
        media = Media(type="film", title="Atelier local", year=2099, tmdb_id=881005, followed=False)
        db.add(media)
        db.flush()
        lib = tmp_path / "vol"
        lib.mkdir()
        path = lib / "local.mkv"
        path.write_bytes(b"\x00" * 200)
        db.add(MediaItem(media_id=media.id, downloaded=True, file_path=str(path)))
        db.flush()
        assert list_wanted_requests(db, settings, now=_utc_now()) == []
    finally:
        db.close()


def test_take_volunteer_without_cherche(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881006)
        _add_cherche(db, settings, origin=REMOTE, cle=cle)
        work_key = _stable_work_key({"type": "film", "tmdb_id": 881006})
        take_wanted_request(db, settings, work_key=work_key, release=False, now=_utc_now())
        media = db.scalar(select(Media).where(Media.tmdb_id == 881006).limit(1))
        assert media is not None and media.followed
        item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
        assert item is not None
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=_utc_now())
        local_cherche = (
            select(HiveSignal)
            .where(HiveSignal.signal == "cherche", HiveSignal.origin_instance_id == _origin_id(settings))
            .limit(1)
        )
        assert db.scalar(local_cherche) is None
        assert db.scalar(select(HiveWantedVolunteer).limit(1)) is not None
        db.commit()
    finally:
        db.close()


def test_volunteer_share_on_file_arrival(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        cle = _film_cle(881007)
        _add_cherche(db, settings, origin=REMOTE, cle=cle)
        work_key = _stable_work_key({"type": "film", "tmdb_id": 881007})
        take_wanted_request(db, settings, work_key=work_key, release=False, now=_utc_now())
        media = db.scalar(select(Media).where(Media.tmdb_id == 881007).limit(1))
        assert media is not None
        item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
        assert item is not None
        lib = tmp_path / "share"
        lib.mkdir()
        fpath = lib / "arrive.mkv"
        fpath.write_bytes(b"\x11" * 300)
        item.downloaded = True
        item.file_path = str(fpath)
        db.flush()
        sync_hive_catalog_item(db, settings, item.id, now=_utc_now())
        assert has_share_intent(db, item.id)
        assert item_file_present(db, item)
        assert db.scalar(select(HiveWantedVolunteer).limit(1)) is None
        db.commit()
    finally:
        db.close()


def _fake_tmdb_title(tmdb_id: int, media_type: str) -> TmdbTitle:
    return TmdbTitle(
        tmdb_id=tmdb_id,
        media_type=media_type,
        title="La Grange Numérique",
        year=2098,
        overview="Fiction test.",
        poster_url="https://image.tmdb.org/t/p/w92/grange-demo.jpg",
        series_status=None,
        release_date=None,
        seasons=(),
    )


def _patch_tmdb(monkeypatch, *, calls: dict[str, int] | None = None):
    class FakeTmdb:
        def __init__(self, _token: str, **_kwargs) -> None:
            pass

        def fetch(self, tmdb_id: int, media_type: str) -> TmdbTitle:
            if calls is not None:
                calls["fetch"] = calls.get("fetch", 0) + 1
            return _fake_tmdb_title(tmdb_id, media_type)

        def close(self) -> None:
            return None

    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: "tok-demo")
    monkeypatch.setattr("mediatracker.hive.wanted_tmdb.TmdbClient", FakeTmdb)

    def _borrow(token: str, _factory) -> tuple[FakeTmdb, bool]:
        return FakeTmdb(token), True

    for path in (
        "mediatracker.integrations.tmdb.borrow_tmdb",
        "mediatracker.core.follow.borrow_tmdb",
    ):
        monkeypatch.setattr(path, _borrow)


def test_wanted_list_tmdb_title_and_cache(client, settings, monkeypatch):
    clear_wanted_tmdb_cache()
    calls: dict[str, int] = {}
    _patch_tmdb(monkeypatch, calls=calls)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(881100))
        rows = list_wanted_requests(db, settings, now=_utc_now())
        assert rows[0]["title"] == "La Grange Numérique"
        assert rows[0]["year"] == 2098
        assert rows[0]["poster_url"] is not None
        list_wanted_requests(db, settings, now=_utc_now())
        assert calls.get("fetch", 0) == 1
    finally:
        db.close()


def test_take_wanted_creates_tmdb_fiche(client, settings, monkeypatch):
    clear_wanted_tmdb_cache()
    _patch_tmdb(monkeypatch)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(881101))
        work_key = _stable_work_key({"type": "film", "tmdb_id": 881101})
        take_wanted_request(db, settings, work_key=work_key, release=False, now=_utc_now())
        media = db.scalar(select(Media).where(Media.tmdb_id == 881101).limit(1))
        assert media is not None
        assert media.title == "La Grange Numérique"
        assert media.year == 2098
        assert media.followed
        db.commit()
    finally:
        db.close()


def test_wanted_fallback_without_token(client, settings, monkeypatch):
    clear_wanted_tmdb_cache()
    monkeypatch.setattr("mediatracker.core.tmdb.token.read_tmdb_token", lambda db, key: None)
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(881102))
        rows = list_wanted_requests(db, settings, now=_utc_now())
        assert rows[0]["title"] == "Fiche TMDB 881102"
    finally:
        db.close()


def test_wanted_api_no_origin_leak(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        _add_cherche(db, settings, origin=REMOTE, cle=_film_cle(881008))
        db.commit()
    finally:
        db.close()
    login(client)
    resp = client.get("/api/v1/hive/requests/wanted")
    assert resp.status_code == 200
    assert REMOTE not in resp.text
    body = resp.json()
    assert len(body["items"]) == 1
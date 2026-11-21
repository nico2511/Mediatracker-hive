"""Catalogue Hive : cartes, regroupement séries, repartage conditionnel."""

from __future__ import annotations

from sqlalchemy import select
from starlette.testclient import TestClient
from tests.conftest import catalog_min_logical_video_bytes, csrf, login, write_sparse_video_file
from tests.test_hive_t3_peer_http import _enable as peer_enable
from tests.test_hive_t5 import NOW, URL_A, URL_B, _join_ruche, _seed_file_on_a
from tests.test_hive_t6 import _enable, _seed_catalogue_row

from mediatracker.db.models import HiveFileFingerprint, HiveShareIntent, Media, MediaItem, Setting
from mediatracker.hive.catalogue_card import stable_series_work_key
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.hive_received_filing import finalize_received_hive_file
from mediatracker.hive.share_policy import HIVE_CATALOG_AUTO_SHARE_KEY

pytest_plugins = ["tests.test_hive_t5"]


def test_catalogue_groups_series_by_tmdb(client):
    login(client)
    _enable(client)
    db = client.app.state.session_factory()
    try:
        for ep in ((1, 1), (1, 2)):
            cle = {"type": "series", "tmdb_id": 77001, "saison": ep[0], "episode": ep[1], "version": "HD+VFF"}
            _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha="a" * 64, joignable=True, taille=0)
    finally:
        db.close()
    page = client.get("/api/v1/hive/catalogue").json()
    series = [w for w in page["items"] if w.get("tmdb_id") == 77001]
    assert len(series) == 1
    row = series[0]
    assert row["work_key"] == stable_series_work_key(77001)
    assert row["episode_count"] == 2
    assert row["season"] is None and row["episode"] is None
    detail = client.get(f"/api/v1/hive/catalogue/{row['work_key']}").json()
    assert len(detail.get("episodes") or []) == 2


def test_catalogue_episode_work_key_still_resolvable(client):
    login(client)
    _enable(client)
    db = client.app.state.session_factory()
    cle = {"type": "series", "tmdb_id": 77002, "saison": 2, "episode": 3, "version": "HD+VFF"}
    try:
        _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha="b" * 64, joignable=False, taille=0)
        wk = _stable_work_key(cle)
    finally:
        db.close()
    one = client.get(f"/api/v1/hive/catalogue/{wk}").json()
    assert one["season"] == 2 and one["episode"] == 3
    assert one.get("card_state") in {"unavailable", "waiting", "available", "library"}


def test_catalogue_card_state_labels_library(client, tmp_path):
    login(client)
    _enable(client)
    db = client.app.state.session_factory()
    cle = {"type": "film", "tmdb_id": 77003, "version": "HD+VFF"}
    try:
        _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha="c" * 64, joignable=True, taille=0)
        media = Media(type="film", title="Carte", year=2099, tmdb_id=77003, followed=True)
        db.add(media)
        db.flush()
        fpath = tmp_path / "card-lib.mkv"
        write_sparse_video_file(fpath, 128_000)
        item = MediaItem(media_id=media.id, downloaded=True, file_path=str(fpath), state="downloaded")
        db.add(item)
        db.commit()
        wk = _stable_work_key(cle)
    finally:
        db.close()
    row = client.get(f"/api/v1/hive/catalogue/{wk}").json()
    assert row["card_state"] == "library"
    assert row["state_label"] == "Dans ma bibliothèque"
    assert row["state_label_short"] == "Bibliothèque"
    assert row["tone"] == "info"


def test_finalize_no_share_intent_when_auto_share_off(transfer_pair, tmp_path):
    app_y, app_x = transfer_pair
    with TestClient(app_y) as cy, TestClient(app_x) as cx:
        peer_enable(cy, "aa", URL_A)
        peer_enable(cx, "bb", URL_B)
        rid_a = _join_ruche(cy, cx, invitee_url=URL_B)
        from tests.conftest import login

        login(cx)
        min_video = catalog_min_logical_video_bytes()
        prefix = b"NO-SHARE-"
        padding = b"z" * max(0, min_video - len(prefix))
        payload = prefix + padding
        assert len(payload) >= min_video
        _origin_y, _fid_y, sha = _seed_file_on_a(app_y, rid_a, payload)
        part = tmp_path / "received.mkv"
        part.write_bytes(payload)
        db_x = app_x.state.session_factory()
        try:
            db_x.merge(Setting(key=HIVE_CATALOG_AUTO_SHARE_KEY, value="false"))
            fiche = {"type": "movie", "tmdb_id": 77004, "title": "NoShare", "year": 2099}
            cle = {"type": "movie", "tmdb_id": 77004, "version": "u+u"}
            media = Media(type="film", title="NoShare", year=2099, tmdb_id=77004, followed=True)
            db_x.add(media)
            db_x.flush()
            item = MediaItem(media_id=media.id, state="missing")
            db_x.add(item)
            db_x.commit()
            err = finalize_received_hive_file(
                db_x,
                app_x.state.settings,
                part=part,
                media=media,
                item=item,
                fiche=fiche,
                cle=cle,
                sha256_hex=sha,
                now=NOW,
            )
            assert err is None
            db_x.commit()
            fp_row = db_x.scalar(select(HiveFileFingerprint).where(HiveFileFingerprint.sha256_hex == sha))
            assert fp_row is not None
            assert db_x.get(HiveShareIntent, fp_row.media_item_id) is None
        finally:
            db_x.close()


def test_series_catalogue_request_requires_episode(client):
    login(client)
    _enable(client)
    db = client.app.state.session_factory()
    cle = {"type": "series", "tmdb_id": 77005, "saison": 1, "episode": 1, "version": "HD+VFF"}
    try:
        _seed_catalogue_row(db, cle=cle, version="HD+VFF", sha="d" * 64, joignable=True, taille=0)
        series_key = stable_series_work_key(77005)
    finally:
        db.close()
    resp = client.post(
        f"/api/v1/hive/catalogue/{series_key}/request",
        json={},
        headers=csrf(client),
    )
    assert resp.status_code == 422

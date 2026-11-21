"""Après téléchargement (Hive ou autre) : retire cherche et sync catalogue."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from tests.test_hive_t2 import _enable_hive, _join_fixture_ruche, _origin_id

from mediatracker.config import Settings
from mediatracker.core.pipeline.queue import _finish_download
from mediatracker.db.models import Download, HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import _cle_document, sign_payload
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.signal import (
    _latest_signal_row,
    build_signal_payload,
    cle_hash,
    next_signal_seq,
    retract_cherche_for_item,
    sync_possede_for_item,
)
from mediatracker.hive.signal_policy import (
    CHERCHE_CATCHUP_AT_KEY,
    maybe_catchup_cherche_for_followed,
)
from mediatracker.hive.signal_requests import local_cherche_active, local_cherche_active_for_work

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def _followed_film(db, *, tmdb_id: int, title: str) -> tuple[Media, MediaItem]:
    media = Media(type="film", title=title, year=2099, tmdb_id=tmdb_id, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id, release_quality="1080p", release_language="fr")
    db.add(item)
    db.flush()
    return media, item


def _publish_cherche(db, settings: Settings, *, media: Media, item: MediaItem, version: str | None = None) -> bytes:
    origin = _origin_id(settings)
    rid = _join_fixture_ruche(db, origin)
    pk = load_private_key(settings)
    assert pk is not None
    cle = _cle_document(media, item)
    if version is not None:
        cle = dict(cle)
        cle["version"] = version
    seq = next_signal_seq(db, rid, origin)
    payload = build_signal_payload(ruche_id=rid, origin_instance_id=origin, seq=seq, signal="cherche", cle=cle)
    db.add(
        HiveSignal(
            ruche_id=rid,
            origin_instance_id=origin,
            seq=seq,
            created_at=NOW,
            signal="cherche",
            cle_hash=cle_hash(cle),
            cle_json=json.dumps(cle),
            payload_json=json.dumps(payload),
            signature_b64=sign_payload(pk, payload),
        )
    )
    db.flush()
    return rid


def test_finish_download_publishes_retire_and_clears_cherche(client, app, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        media, item = _followed_film(db, tmdb_id=991201, title="Exemple retrait A")
        moment = NOW
        rid = _publish_cherche(db, settings, media=media, item=item)
        cle = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)

        movies = Path(settings.data_dir) / "media" / "Movies"
        dest = movies / "Exemple retrait A (2099)" / "Exemple retrait A (2099).mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")
        source = Path(settings.data_dir) / "downloads" / "incomplete" / "dl-a.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        download = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()

        _finish_download(db, download, item, media, dest, source, moment, settings, store_release=False)
        db.flush()

        assert media.followed is False
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle)
        assert latest is not None and latest.signal in {"retire", "possede"}
        db.commit()
    finally:
        db.close()


def test_retract_all_slot_versions_on_download(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        media, item = _followed_film(db, tmdb_id=991202, title="Exemple retrait B")
        cle_primary = _cle_document(media, item)
        _publish_cherche(db, settings, media=media, item=item, version=cle_primary["version"])
        _publish_cherche(db, settings, media=media, item=item, version="720p+en")
        rid = _join_fixture_ruche(db, origin)
        assert local_cherche_active_for_work(db, ruche_id=rid, origin=origin, cle=cle_primary)

        movies = Path(settings.data_dir) / "media" / "Movies"
        dest = movies / "Exemple retrait B (2099)" / "Exemple retrait B (2099).mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")
        source = Path(settings.data_dir) / "downloads" / "incomplete" / "dl-b.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        download = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()

        _finish_download(db, download, item, media, dest, source, NOW, settings, store_release=False)
        db.flush()

        assert not local_cherche_active_for_work(db, ruche_id=rid, origin=origin, cle=cle_primary)
        cherche_left = db.scalars(
            select(HiveSignal).where(
                HiveSignal.ruche_id == rid,
                HiveSignal.origin_instance_id == origin,
                HiveSignal.signal == "cherche",
            )
        ).all()
        assert cherche_left == []
        db.commit()
    finally:
        db.close()


def test_catchup_retracts_cherche_when_already_unfollowed_with_file(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        media, item = _followed_film(db, tmdb_id=991203, title="Exemple retrait C")
        cle = _cle_document(media, item)
        rid = _publish_cherche(db, settings, media=media, item=item)
        movies = Path(settings.data_dir) / "media" / "Movies"
        dest = movies / "Exemple retrait C (2099)" / "Exemple retrait C (2099).mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")
        item.file_path = str(dest)
        item.downloaded = True
        item.state = "downloaded"
        media.followed = False
        db.flush()
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)

        moment = NOW
        db.merge(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=(moment - timedelta(hours=1)).isoformat()))
        assert maybe_catchup_cherche_for_followed(db, settings, now=moment)
        db.flush()
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        db.commit()
    finally:
        db.close()


def test_series_episode_download_retracts_cherche_while_series_still_followed(client, app, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        media = Media(type="series", title="Exemple série", year=2099, tmdb_id=991204, followed=True)
        db.add(media)
        db.flush()
        item_ep1 = MediaItem(media_id=media.id, season=1, episode=1, release_quality="1080p", release_language="fr")
        item_ep2 = MediaItem(media_id=media.id, season=1, episode=2, release_quality="1080p", release_language="fr")
        db.add_all([item_ep1, item_ep2])
        db.flush()
        cle_ep1 = _cle_document(media, item_ep1)
        rid = _publish_cherche(db, settings, media=media, item=item_ep1)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_ep1)

        series = Path(settings.data_dir) / "media" / "TV Shows" / "Exemple série (2099)"
        dest = series / "Season 01" / "Exemple série - S01E01.mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")
        source = Path(settings.data_dir) / "downloads" / "incomplete" / "dl-ep1.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        download = Download(media_item_id=item_ep1.id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()

        _finish_download(db, download, item_ep1, media, dest, source, NOW, settings, store_release=False)
        db.flush()

        assert media.followed is True
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_ep1)
        db.commit()
    finally:
        db.close()


def test_retract_cherche_for_item_publishes_retire(client, settings):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        media, item = _followed_film(db, tmdb_id=991206, title="Exemple retract")
        cle = _cle_document(media, item)
        rid = _publish_cherche(db, settings, media=media, item=item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)

        retract_cherche_for_item(db, settings, media=media, item=item, now=NOW)
        db.flush()

        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle)
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle)
        assert latest is not None and latest.signal == "retire"
        db.commit()
    finally:
        db.close()


def test_possede_published_for_unfollowed_film_with_file(client, settings, tmp_path):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media = Media(type="film", title="Exemple possede", year=2099, tmdb_id=991207, followed=False)
        db.add(media)
        db.flush()
        fpath = tmp_path / "owned.mkv"
        fpath.write_bytes(b"data")
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="fr",
        )
        db.add(item)
        db.flush()
        cle = _cle_document(media, item)
        sync_possede_for_item(db, settings, media=media, item=item, now=NOW)
        db.flush()
        latest = _latest_signal_row(db, ruche_id=rid, origin_id=origin, cle=cle)
        assert latest is not None and latest.signal == "possede"
        db.commit()
    finally:
        db.close()


def test_finish_download_succeeds_when_hive_catalog_sync_fails(client, app, settings, tmp_path, monkeypatch):
    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        media, item = _followed_film(db, tmdb_id=991205, title="Exemple sync")
        movies = Path(settings.data_dir) / "media" / "Movies"
        dest = movies / "Exemple sync (2099)" / "Exemple sync (2099).mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")
        source = Path(settings.data_dir) / "downloads" / "incomplete" / "dl-sync.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        download = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()

        def _boom(*_a, **_k):
            raise RuntimeError("sync indisponible")

        monkeypatch.setattr("mediatracker.hive.catalog.sync_hive_catalog_item", _boom)
        _finish_download(db, download, item, media, dest, source, NOW, settings, store_release=False)
        db.flush()
        assert item.downloaded is True
        assert item.file_path == str(dest)
        db.commit()
    finally:
        db.close()


def test_finish_download_commits_when_hive_catalog_sync_raises_sql(client, app, settings, tmp_path, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError

    _enable_hive(client)
    db = client.app.state.session_factory()
    try:
        media, item = _followed_film(db, tmdb_id=991208, title="Exemple sql")
        movies = Path(settings.data_dir) / "media" / "Movies"
        dest = movies / "Exemple sql (2099)" / "Exemple sql (2099).mkv"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"video")
        source = Path(settings.data_dir) / "downloads" / "incomplete" / "dl-sql.mkv"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"video")
        download = Download(media_item_id=item.id, state="post_processing", filename=source.name, error=str(source))
        db.add(download)
        db.flush()

        def _sql_boom(*_a, **_k):
            raise SQLAlchemyError("simulated hive sync")

        monkeypatch.setattr("mediatracker.hive.catalog.sync_hive_catalog_item", _sql_boom)
        _finish_download(db, download, item, media, dest, source, NOW, settings, store_release=False)
        db.flush()
        assert item.downloaded is True
        db.commit()
        persisted = client.app.state.session_factory()
        try:
            row = persisted.get(MediaItem, item.id)
            assert row is not None and row.downloaded is True
            assert row.file_path == str(dest)
        finally:
            persisted.close()
    finally:
        db.close()

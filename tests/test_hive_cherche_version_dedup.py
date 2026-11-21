"""Cherche : retrait des anciennes versions et dédoublonnage des demandes reçues."""

from __future__ import annotations

import base64
import json
import json as json_mod
from datetime import datetime, timezone

from sqlalchemy import select
from tests.test_hive_t2 import _join_fixture_ruche, _origin_id

from mediatracker.db.models import HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import _cle_document, ensure_hive_release_metadata, sign_payload
from mediatracker.hive.crypto_id import (
    build_name_document,
    generate_keypair,
    hive_public_id_from_public_key,
    sign_document,
)
from mediatracker.hive.identity import (
    HIVE_ENABLED_KEY,
    HIVE_INSTANCE_NAME_KEY,
    HIVE_NAME_DOCUMENT_KEY,
    HIVE_NAME_SIGNATURE_KEY,
    load_private_key,
    write_private_key,
)
from mediatracker.hive.signal import (
    build_signal_payload,
    cle_hash,
    next_signal_seq,
    publish_cherche_for_item,
)
from mediatracker.hive.signal_requests import list_received_requests, local_cherche_active


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _enable_hive_in_db(db, settings) -> None:
    moment = _utc_now()
    if load_private_key(settings) is None:
        private_key, public_key = generate_keypair()
        write_private_key(settings, private_key)
        instance_id = hive_public_id_from_public_key(public_key)
        document = build_name_document(instance_id=instance_id, name="ab", signed_at=moment)
        signature = sign_document(private_key, document)
        doc_json = json_mod.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        sig_b64 = base64.b64encode(signature).decode("ascii")
        for key, val in (
            (HIVE_INSTANCE_NAME_KEY, "ab"),
            (HIVE_NAME_DOCUMENT_KEY, doc_json),
            (HIVE_NAME_SIGNATURE_KEY, sig_b64),
        ):
            db.merge(Setting(key=key, value=val, is_secret=False, updated_at=moment))
    db.merge(Setting(key=HIVE_ENABLED_KEY, value="true", is_secret=False, updated_at=moment))
    db.flush()


def _followed_movie(db, title: str, tmdb_id: int) -> tuple[Media, MediaItem]:
    media = Media(type="movie", title=title, year=2099, tmdb_id=tmdb_id, followed=True)
    db.add(media)
    db.flush()
    item = MediaItem(media_id=media.id)
    db.add(item)
    db.flush()
    return media, item


def test_publish_cherche_retracts_previous_version_on_slot_key_change(client, settings):
    db = client.app.state.session_factory()
    try:
        _enable_hive_in_db(db, settings)
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        media, item = _followed_movie(db, "Œuvre version A", 880201)
        media.pref_quality = "720p"
        media.pref_language = "VOST"
        moment = _utc_now()
        ensure_hive_release_metadata(media, item)
        publish_cherche_for_item(db, settings, media=media, item=item, now=moment)
        db.flush()
        cle_old = _cle_document(media, item)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_old)

        media.pref_quality = "1080p"
        media.pref_language = "VFF"
        item.release_quality = None
        item.release_language = None
        ensure_hive_release_metadata(media, item)
        publish_cherche_for_item(db, settings, media=media, item=item, now=moment)
        db.flush()
        cle_new = _cle_document(media, item)
        assert cle_hash(cle_old) != cle_hash(cle_new)
        assert not local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_old)
        assert local_cherche_active(db, ruche_id=rid, origin=origin, cle=cle_new)
        still_cherche = db.scalar(
            select(HiveSignal).where(
                HiveSignal.ruche_id == rid,
                HiveSignal.origin_instance_id == origin,
                HiveSignal.signal == "cherche",
            )
        )
        assert still_cherche is not None
        assert still_cherche.cle_hash == cle_hash(cle_new)
        db.commit()
    finally:
        db.close()


def test_received_requests_dedup_by_work_merges_origins_and_versions(client, settings, tmp_path):
    db = client.app.state.session_factory()
    try:
        _enable_hive_in_db(db, settings)
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        pk = load_private_key(settings)
        assert pk is not None
        origin_a = "remotePeerPlateau880298"
        origin_b = "remotePeerPlateau880299"
        cle_a = {"type": "movie", "tmdb_id": 880298, "version": "720p+VOST"}
        cle_b = {"type": "movie", "tmdb_id": 880298, "version": "1080p+VFF"}
        for remote_origin, cle in ((origin_a, cle_a), (origin_b, cle_b)):
            seq = next_signal_seq(db, rid, remote_origin)
            payload = build_signal_payload(
                ruche_id=rid, origin_instance_id=remote_origin, seq=seq, signal="cherche", cle=cle
            )
            db.add(
                HiveSignal(
                    ruche_id=rid,
                    origin_instance_id=remote_origin,
                    seq=seq,
                    created_at=_utc_now(),
                    signal="cherche",
                    cle_hash=cle_hash(cle),
                    cle_json=json.dumps(cle),
                    payload_json=json.dumps(payload),
                    signature_b64=sign_payload(pk, payload),
                )
            )
        media_match = Media(type="film", title="Fichier local C", year=2098, tmdb_id=880298, followed=False)
        db.add(media_match)
        db.flush()
        lib = tmp_path / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        fpath = lib / "local-dedup.mkv"
        fpath.write_bytes(b"\x02" * 128)
        db.add(
            MediaItem(
                media_id=media_match.id,
                downloaded=True,
                file_path=str(fpath),
                release_quality="1080p",
                release_language="VFF",
            )
        )
        db.flush()
        rows = list_received_requests(db, settings)
        assert len(rows) == 1
        row = rows[0]
        assert row["requester_count"] == 2
        assert set(row["requested_versions"]) == {"720p+VOST", "1080p+VFF"}
        db.commit()
    finally:
        db.close()
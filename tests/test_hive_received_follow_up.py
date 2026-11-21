"""Demandes reçues : suivi factuel après acceptation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from tests.test_hive_cherche_version_dedup import _enable_hive_in_db, _utc_now
from tests.test_hive_t2 import _join_fixture_ruche, _origin_id

from mediatracker.db.models import HiveFileFingerprint, HiveSignal, Media, MediaItem, Setting
from mediatracker.hive.catalog import sign_payload
from mediatracker.hive.identity import HIVE_PUBLIC_ADDRESS_KEY, load_private_key
from mediatracker.hive.signal import build_signal_payload, cle_hash, next_signal_seq
from mediatracker.hive.signal_requests import accept_received_request, list_received_requests, opaque_request_id

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


def test_received_request_stays_visible_while_fingerprint_preparing(client, settings, tmp_path):
    db = client.app.state.session_factory()
    try:
        _enable_hive_in_db(db, settings)
        db.merge(
            Setting(
                key=HIVE_PUBLIC_ADDRESS_KEY,
                value="https://peer.exemple.test",
                is_secret=False,
                updated_at=NOW,
            )
        )
        origin = _origin_id(settings)
        rid = _join_fixture_ruche(db, origin)
        pk = load_private_key(settings)
        assert pk is not None
        remote = "remotePeerPlateau880600"
        cle = {"type": "film", "tmdb_id": 880600, "version": "1080p+VFF"}
        seq = next_signal_seq(db, rid, remote)
        payload = build_signal_payload(
            ruche_id=rid, origin_instance_id=remote, seq=seq, signal="cherche", cle=cle
        )
        db.add(
            HiveSignal(
                ruche_id=rid,
                origin_instance_id=remote,
                seq=seq,
                created_at=_utc_now(),
                signal="cherche",
                cle_hash=cle_hash(cle),
                cle_json=json.dumps(cle),
                payload_json=json.dumps(payload),
                signature_b64=sign_payload(pk, payload),
            )
        )
        media = Media(type="film", title="Fichier local", year=2099, tmdb_id=880600, followed=False)
        db.add(media)
        db.flush()
        lib = tmp_path / "lib"
        lib.mkdir(parents=True, exist_ok=True)
        fpath = lib / "share.mkv"
        fpath.write_bytes(b"\x03" * 256)
        item = MediaItem(
            media_id=media.id,
            downloaded=True,
            file_path=str(fpath),
            release_quality="1080p",
            release_language="VFF",
        )
        db.add(item)
        db.flush()
        db.add(
            HiveFileFingerprint(
                media_item_id=item.id,
                path=str(fpath),
                size=256,
                partial_size=64,
                sha256_hex="",
            )
        )
        db.flush()

        before = list_received_requests(db, settings)
        assert len(before) == 1 and before[0]["status"] == "pending"
        req_id = opaque_request_id(ruche_id=rid, requester_origin=remote, cle=cle)
        accept_received_request(db, settings, request_id=req_id, now=NOW)
        db.flush()
        after = list_received_requests(db, settings)
        assert len(after) == 1
        assert after[0]["status"] in ("preparing", "sharing")
        db.commit()
    finally:
        db.close()

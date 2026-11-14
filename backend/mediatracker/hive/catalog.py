"""Catalogue Hive local : exclusions, entrées signées, pierres tombales (T2)."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.file_status import FileStatusIndex
from mediatracker.db.models import (
    HiveEntry,
    HiveFileFingerprint,
    HiveMember,
    HiveShareExclusion,
    HiveVersions,
    Media,
    MediaItem,
)
from mediatracker.hive.catalog_validity import local_offer_publishable
from mediatracker.hive.crypto_id import hive_public_id_from_public_key, sign_document, verify_document_signature
from mediatracker.hive.fingerprint import _stat_fingerprint, ensure_fingerprint_row_for_item
from mediatracker.hive.gossip_burst import mark_local_catalog_change
from mediatracker.hive.identity import (
    HIVE_PUBLIC_ADDRESS_KEY,
    _read_setting_text,  # noqa: SLF001
    load_private_key,
)
from mediatracker.hive.share_exclusions import RUCHE_EXCLUDE_ALL
from mediatracker.hive.share_policy import (
    abandon_orphan_fingerprint_work,
    grant_share_intent,
    has_share_intent,
    hive_catalog_auto_share_enabled,
    item_wants_hive_share,
    revoke_share_intent,
)

TOMBSTONE_RETENTION = timedelta(days=30)
TMDB_POSTER_PREFIX = "https://image.tmdb.org/"


def _gossip_poster_url(url: str | None) -> str | None:
    if not url or not str(url).startswith(TMDB_POSTER_PREFIX):
        return None
    return str(url)


def strip_hive_keep_flags(db: Session) -> None:
    """Retire uniquement les « Garder » posés par Hive (origine hive)."""
    db.execute(update(Media).where(Media.keep_source == "hive").values(keep=False, keep_source=None))
    db.execute(
        update(MediaItem).where(MediaItem.keep_source == "hive").values(keep=False, keep_source=None)
    )


def _public_address_configured(db: Session) -> bool:
    return bool(_read_setting_text(db, HIVE_PUBLIC_ADDRESS_KEY).strip())


def _origin_for_signing(settings: Settings):
    pk = load_private_key(settings)
    if pk is None:
        return None, None
    pub = pk.public_key()
    return pk, hive_public_id_from_public_key(pub)


def member_ruche_ids_for_instance(db: Session, instance_id: str) -> list[bytes]:
    rows = list(
        db.scalars(select(HiveMember.ruche_id).where(HiveMember.member_instance_id == instance_id)).all()
    )
    return list(dict.fromkeys(rows))


def is_excluded_from_ruche(db: Session, media_item_id: int, ruche_id: bytes) -> bool:
    rows = list(
        db.scalars(select(HiveShareExclusion).where(HiveShareExclusion.media_item_id == media_item_id)).all()
    )
    for row in rows:
        if row.ruche_id == RUCHE_EXCLUDE_ALL or row.ruche_id == ruche_id:
            return True
    return False


def item_is_shareable_base(db: Session, item: MediaItem, *, hive_enabled: bool) -> bool:
    """Fichier présent + empreinte prête + adresse publique (sans regarder les exclusions par ruche)."""
    if not hive_enabled:
        return False
    if not _public_address_configured(db):
        return False
    idx = FileStatusIndex.load(db, [item])
    status = idx.for_item(item)
    present = status == "present"
    if not present and status not in {"trash", "trashed"} and item.downloaded and item.file_path:
        from pathlib import Path

        present = Path(item.file_path).is_file()
    if not present:
        return False
    fp = db.get(HiveFileFingerprint, item.id) if item.id is not None else None
    return fp is not None and bool(fp.sha256_hex)


def any_ruche_wants_share(
    db: Session, item_id: int, ruche_ids: list[bytes], *, shareable_base: bool
) -> bool:
    if not shareable_base:
        return False
    return any(not is_excluded_from_ruche(db, item_id, rid) for rid in ruche_ids)


def maybe_set_hive_keep(
    db: Session, media: Media, item: MediaItem, *, want_hive_keep: bool
) -> None:
    if want_hive_keep:
        if not item.keep and not media.keep:
            item.keep = True
            item.keep_source = "hive"
        return
    if item.keep_source == "hive":
        item.keep = False
        item.keep_source = None


def next_seq(db: Session, ruche_id: bytes, origin_instance_id: str) -> int:
    m = db.scalar(
        select(func.max(HiveEntry.seq)).where(
            HiveEntry.ruche_id == ruche_id,
            HiveEntry.origin_instance_id == origin_instance_id,
        )
    )
    seq = 1 if m is None else int(m) + 1
    row = db.get(HiveVersions, (ruche_id, origin_instance_id))
    if row is None:
        db.add(HiveVersions(ruche_id=ruche_id, origin_instance_id=origin_instance_id, seq=seq))
    else:
        row.seq = seq
    return seq


# Clé « unknown+unknown » : annonces legacy sans métadonnées release ; le classement
# profil accepte cette clé avec une pénalité fixe (voir source_catalog), pas via les prefs fiche.
def ensure_hive_release_metadata(media: Media, item: MediaItem) -> None:
    """Renseigne release_quality / release_language avant publication Hive."""
    from mediatracker.library.release_name import release_tokens

    q = (item.release_quality or "").strip()
    lang = (item.release_language or "").strip()
    if q and lang and q.casefold() != "unknown" and lang.casefold() != "unknown":
        return

    parsed_q: str | None = None
    parsed_lang: str | None = None
    for name in (item.original_name, Path(item.file_path or "").name if item.file_path else None):
        if not name:
            continue
        candidate_q, candidate_lang = release_tokens(str(name))
        parsed_q = parsed_q or candidate_q
        parsed_lang = parsed_lang or candidate_lang
        if parsed_q and parsed_lang:
            break

    item.release_quality = parsed_q or media.pref_quality or q or "unknown"
    item.release_language = parsed_lang or media.pref_language or lang or "unknown"


def _version_label(item: MediaItem) -> str:
    q = (item.release_quality or "unknown").strip()
    lang = (item.release_language or "unknown").strip()
    return f"{q}+{lang}"


def _cle_document(media: Media, item: MediaItem) -> dict:
    doc: dict = {"type": media.type, "tmdb_id": media.tmdb_id, "version": _version_label(item)}
    if media.type == "series":
        doc["saison"] = item.season
        doc["episode"] = item.episode
    return doc


def _fiche_document(media: Media, item: MediaItem, *, taille: int | None = None) -> dict:
    doc: dict = {
        "type": media.type,
        "tmdb_id": media.tmdb_id,
        "title": media.title,
        "year": media.year,
        "season": item.season,
        "episode": item.episode,
    }
    poster = _gossip_poster_url(media.poster_url)
    if poster:
        doc["poster_url"] = poster
    overview = (media.overview or "").strip()
    if overview:
        doc["overview"] = overview[:4000]
    if isinstance(taille, int) and taille > 0:
        doc["taille"] = taille
    return doc


def _file_id_for_ruche(ruche_id: bytes, origin_id: str, media_item_id: int) -> bytes:
    import hashlib

    h = hashlib.sha256()
    h.update(ruche_id)
    h.update(origin_id.encode("utf-8"))
    h.update(b"\0")
    h.update(str(media_item_id).encode("ascii"))
    return h.digest()[:16]


def build_catalog_payload(
    *,
    ruche_id: bytes,
    origin_instance_id: str,
    seq: int,
    cle: dict,
    fiche: dict,
    sha256_hex: str,
    file_id: bytes,
    etat: str,
    taille: int | None = None,
    file_mtime_ns: int | None = None,
) -> dict:
    payload: dict = {
        "v": 1,
        "ruche_id": ruche_id.hex(),
        "origine": origin_instance_id,
        "seq": seq,
        "cle": cle,
        "fiche": fiche,
        "sha256": sha256_hex,
        "file_id": file_id.hex(),
        "etat": etat,
    }
    if isinstance(taille, int) and taille > 0:
        payload["taille"] = taille
    if isinstance(file_mtime_ns, int) and file_mtime_ns > 0:
        payload["mtime_ns"] = file_mtime_ns
    return payload


def sign_payload(private_key, payload: dict) -> str:
    sig = sign_document(private_key, payload)
    return base64.b64encode(sig).decode("ascii")


def verify_catalog_payload(
    settings: Settings, *, expected_ruche_id: bytes, payload: dict, signature_b64: str
) -> None:
    sk = load_private_key(settings)
    if sk is None:
        raise ValueError("no_private_key")
    pub = sk.public_key()
    rid = payload.get("ruche_id")
    if not isinstance(rid, str) or bytes.fromhex(rid) != expected_ruche_id:
        raise ValueError("bad_ruche")
    sig = base64.b64decode(signature_b64.encode("ascii"), validate=True)
    verify_document_signature(pub, payload, sig)


def _latest_entry_for_item(
    db: Session, ruche_id: bytes, origin_id: str, media_item_id: int
) -> HiveEntry | None:
    return db.scalar(
        select(HiveEntry)
        .where(
            HiveEntry.ruche_id == ruche_id,
            HiveEntry.origin_instance_id == origin_id,
            HiveEntry.media_item_id == media_item_id,
        )
        .order_by(HiveEntry.seq.desc())
        .limit(1)
    )


def _published_entry_payload_parts(entry: HiveEntry) -> tuple[dict, dict, dict]:
    cle: dict = {}
    fiche: dict = {}
    body: dict = {}
    if entry.cle_json:
        try:
            parsed = json.loads(entry.cle_json)
            if isinstance(parsed, dict):
                cle = parsed
        except json.JSONDecodeError:
            pass
    if entry.fiche_json:
        try:
            parsed = json.loads(entry.fiche_json)
            if isinstance(parsed, dict):
                fiche = parsed
        except json.JSONDecodeError:
            pass
    if entry.payload_json:
        try:
            parsed = json.loads(entry.payload_json)
            if isinstance(parsed, dict):
                body = parsed
        except json.JSONDecodeError:
            pass
    return cle, fiche, body


def _should_skip_identical_publish(
    last: HiveEntry,
    *,
    media: Media,
    item: MediaItem,
    fingerprint_hex: str,
    file_mtime_ns: int | None,
    taille: int | None,
) -> bool:
    """Ne pas republier si SHA, mtime et métadonnées catalogue sont inchangés."""
    if last.etat != "disponible" or last.sha256_hex != fingerprint_hex:
        return False
    cle_old, fiche_old, body = _published_entry_payload_parts(last)
    cle_new = _cle_document(media, item)
    fiche_new = _fiche_document(media, item, taille=taille)
    if cle_old.get("version") != cle_new.get("version"):
        return False
    if fiche_old.get("poster_url") != fiche_new.get("poster_url"):
        return False
    if fiche_old.get("overview") != fiche_new.get("overview"):
        return False
    published_mtime = body.get("mtime_ns")
    if file_mtime_ns is not None and published_mtime != file_mtime_ns:
        return False
    return True


def _persist_entry(db: Session, *, ruche_id: bytes, payload: dict, signature_b64: str, media_item_id: int) -> None:
    origin = payload["origine"]
    entry = HiveEntry(
        ruche_id=ruche_id,
        origin_instance_id=origin,
        seq=int(payload["seq"]),
        cle_json=json.dumps(payload["cle"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        fiche_json=json.dumps(payload["fiche"], ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        sha256_hex=payload["sha256"],
        file_id=bytes.fromhex(payload["file_id"]),
        etat=payload["etat"],
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        signature_b64=signature_b64,
        media_item_id=media_item_id,
        tombstone_at=None,
    )
    db.add(entry)


def publish_available(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    media: Media,
    item: MediaItem,
    fingerprint_hex: str,
    now: datetime,
) -> None:
    private_key, origin_id = _origin_for_signing(settings)
    if private_key is None or item.id is None:
        return
    ensure_hive_release_metadata(media, item)
    file_id = _file_id_for_ruche(ruche_id, origin_id, item.id)
    fp = db.get(HiveFileFingerprint, item.id) if item.id is not None else None
    if not local_offer_publishable(media=media, item=item, fp=fp, fingerprint_hex=fingerprint_hex):
        publish_tombstone(db, settings, ruche_id=ruche_id, media=media, item=item, now=now)
        return
    taille = int(fp.size) if fp is not None and int(fp.size or 0) > 0 else None
    file_mtime_ns = int(fp.mtime_ns) if fp is not None and int(fp.mtime_ns or 0) > 0 else None
    cle_new = _cle_document(media, item)
    last = _latest_entry_for_item(db, ruche_id, origin_id, item.id)
    if last is not None and last.etat == "disponible" and last.media_item_id == item.id:
        cle_old, _, _ = _published_entry_payload_parts(last)
        if cle_old.get("version") != cle_new.get("version"):
            publish_tombstone(db, settings, ruche_id=ruche_id, media=media, item=item, now=now)
            db.flush()
            last = _latest_entry_for_item(db, ruche_id, origin_id, item.id)
    if (
        last is not None
        and last.media_item_id == item.id
        and _should_skip_identical_publish(
            last,
            media=media,
            item=item,
            fingerprint_hex=fingerprint_hex,
            file_mtime_ns=file_mtime_ns,
            taille=taille,
        )
    ):
        return
    seq = next_seq(db, ruche_id, origin_id)
    payload = build_catalog_payload(
        ruche_id=ruche_id,
        origin_instance_id=origin_id,
        seq=seq,
        cle=_cle_document(media, item),
        fiche=_fiche_document(media, item, taille=taille),
        sha256_hex=fingerprint_hex,
        file_id=file_id,
        etat="disponible",
        taille=taille,
        file_mtime_ns=file_mtime_ns,
    )
    sig = sign_payload(private_key, payload)
    _persist_entry(db, ruche_id=ruche_id, payload=payload, signature_b64=sig, media_item_id=item.id)
    from mediatracker.hive.peer_gossip import record_reachability

    record_reachability(db, ruche_id=ruche_id, origin_id=origin_id, now=now)
    mark_local_catalog_change(db, ruche_id=ruche_id, now=now)


def publish_tombstone(
    db: Session,
    settings: Settings,
    *,
    ruche_id: bytes,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> None:
    private_key, origin_id = _origin_for_signing(settings)
    if private_key is None or item.id is None:
        return
    last = _latest_entry_for_item(db, ruche_id, origin_id, item.id)
    if last is None or last.etat == "retire":
        return
    seq = next_seq(db, ruche_id, origin_id)
    if last.payload_json:
        prev = json.loads(last.payload_json)
    else:
        prev = build_catalog_payload(
            ruche_id=ruche_id,
            origin_instance_id=origin_id,
            seq=last.seq,
            cle=_cle_document(media, item),
            fiche=_fiche_document(media, item),
            sha256_hex=last.sha256_hex or "",
            file_id=last.file_id or _file_id_for_ruche(ruche_id, origin_id, item.id),
            etat="disponible",
        )
    payload = dict(prev)
    payload["seq"] = seq
    payload["etat"] = "retire"
    sig = sign_payload(private_key, payload)
    entry = HiveEntry(
        ruche_id=ruche_id,
        origin_instance_id=origin_id,
        seq=seq,
        cle_json=last.cle_json,
        fiche_json=last.fiche_json,
        sha256_hex=last.sha256_hex,
        file_id=last.file_id,
        etat="retire",
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        signature_b64=sig,
        media_item_id=item.id,
        tombstone_at=now,
    )
    db.add(entry)
    mark_local_catalog_change(db, ruche_id=ruche_id, now=now)


def purge_old_tombstones(db: Session, *, now: datetime) -> int:
    cutoff = now - TOMBSTONE_RETENTION
    old_ids = list(
        db.scalars(
            select(HiveEntry.id).where(
                HiveEntry.etat == "retire",
                HiveEntry.tombstone_at.is_not(None),
                HiveEntry.tombstone_at < cutoff,
            )
        ).all()
    )
    if not old_ids:
        return 0
    db.execute(delete(HiveEntry).where(HiveEntry.id.in_(old_ids)))
    return len(old_ids)


def _fingerprint_hash_in_progress(fp: HiveFileFingerprint | None) -> bool:
    """Empreinte démarrée mais pas encore terminée."""
    return fp is not None and not fp.sha256_hex


def _fingerprint_matches_present_file(item: MediaItem, fp: HiveFileFingerprint) -> bool:
    path_str = item.file_path or fp.path
    if not path_str:
        return False
    path = Path(path_str)
    if not path.is_file():
        return False
    try:
        size, mtime_ns = _stat_fingerprint(path)
    except OSError:
        return False
    resolved = str(path.resolve())
    return fp.path == resolved and int(fp.size or 0) == size and int(fp.mtime_ns or 0) == mtime_ns


def _should_preserve_disponible_entry(
    db: Session,
    *,
    item: MediaItem,
    fp: HiveFileFingerprint | None,
    excluded: bool,
    ruche_id: bytes,
    origin_id: str,
) -> bool:
    """Garde une entrée « disponible » seulement si l'offre est encore valable."""
    if excluded or item.id is None or fp is None:
        return False
    idx = FileStatusIndex.load(db, [item])
    if idx.for_item(item) not in {"present"}:
        return False
    last = _latest_entry_for_item(db, ruche_id, origin_id, item.id)
    if last is None or last.etat != "disponible":
        return False
    if not _fingerprint_matches_present_file(item, fp):
        return False
    if fp.sha256_hex:
        return last.sha256_hex == fp.sha256_hex
    if not _fingerprint_hash_in_progress(fp):
        return False
    if not last.sha256_hex:
        return False
    published: dict = {}
    if last.payload_json:
        try:
            published = json.loads(last.payload_json)
        except json.JSONDecodeError:
            published = {}
        taille = published.get("taille")
        if isinstance(taille, int) and int(fp.size or 0) != taille:
            return False
    if last.sha256_hex and not fp.sha256_hex:
        published_mtime = published.get("mtime_ns")
        if not isinstance(published_mtime, int):
            return False
        if int(fp.mtime_ns or 0) != published_mtime:
            return False
        return int(fp.partial_size or 0) > 0
    if not last.sha256_hex and int(fp.partial_size or 0) > 0:
        return True
    return False


def catalog_entry_local_for_item(db: Session, settings: Settings, item: MediaItem) -> bool:
    """Entrée signée « disponible » valide pour chaque ruche membre non exclue (preuve locale, pas pair)."""
    from mediatracker.hive.identity import hive_enabled_in_db
    from mediatracker.hive.share_exclusions import list_excluded_ruche_ids

    if not hive_enabled_in_db(db) or item.id is None:
        return False
    if not _public_address_configured(db):
        return False
    _, origin_id = _origin_for_signing(settings)
    if origin_id is None:
        return False
    if not item_wants_hive_share(db, item.id):
        return False
    exclude_all, _ = list_excluded_ruche_ids(db, item.id)
    if exclude_all:
        return False
    ruche_ids = member_ruche_ids_for_instance(db, origin_id)
    targets = [rid for rid in ruche_ids if not is_excluded_from_ruche(db, item.id, rid)]
    if not targets:
        return False
    fp = db.get(HiveFileFingerprint, item.id)
    if fp is None:
        return False
    shareable = item_is_shareable_base(db, item, hive_enabled=True)
    for rid in targets:
        last = _latest_entry_for_item(db, rid, origin_id, item.id)
        if last is None or last.etat != "disponible":
            return False
        if shareable and fp.sha256_hex and last.sha256_hex == fp.sha256_hex:
            if not _fingerprint_matches_present_file(item, fp):
                return False
            continue
        if _should_preserve_disponible_entry(
            db, item=item, fp=fp, excluded=False, ruche_id=rid, origin_id=origin_id
        ):
            continue
        return False
    return True


def _sync_one_media_item_hive_catalog(
    db: Session,
    settings: Settings,
    *,
    item: MediaItem,
    media: Media,
    ruche_ids: list[bytes],
    origin_id: str,
    moment: datetime,
) -> None:
    wants_share = item_wants_hive_share(db, item.id)
    if wants_share:
        ensure_fingerprint_row_for_item(db, settings, item, now=moment)
    elif item.id is not None:
        idx = FileStatusIndex.load(db, [item])
        if idx.for_item(item) != "present":
            row = db.get(HiveFileFingerprint, item.id)
            if row is not None:
                db.delete(row)
    shareable_base = item_is_shareable_base(db, item, hive_enabled=True)

    want_hive_keep = wants_share and shareable_base and any(
        not is_excluded_from_ruche(db, item.id, rid) for rid in ruche_ids
    )
    maybe_set_hive_keep(db, media, item, want_hive_keep=want_hive_keep)

    if item.id is None:
        return

    for rid in ruche_ids:
        excluded = is_excluded_from_ruche(db, item.id, rid)
        fp = db.get(HiveFileFingerprint, item.id)
        if excluded:
            last = _latest_entry_for_item(db, rid, origin_id, item.id)
            if last is not None and last.etat == "disponible":
                publish_tombstone(db, settings, ruche_id=rid, media=media, item=item, now=moment)
            continue
        if wants_share and shareable_base and fp and fp.sha256_hex:
            publish_available(
                db,
                settings,
                ruche_id=rid,
                media=media,
                item=item,
                fingerprint_hex=fp.sha256_hex,
                now=moment,
            )
        elif _should_preserve_disponible_entry(
            db, item=item, fp=fp, excluded=excluded, ruche_id=rid, origin_id=origin_id
        ):
            continue
        else:
            last = _latest_entry_for_item(db, rid, origin_id, item.id)
            if last is not None and last.etat == "disponible":
                publish_tombstone(db, settings, ruche_id=rid, media=media, item=item, now=moment)


def sync_hive_catalog_item(
    db: Session,
    settings: Settings,
    media_item_id: int,
    *,
    now: datetime | None = None,
) -> None:
    """Sync catalogue Hive pour un seul épisode (chemin API partage, sans scan global)."""
    from mediatracker.hive.identity import hive_enabled_in_db
    from mediatracker.hive.share_exclusions import list_excluded_ruche_ids

    moment = now or datetime.now(timezone.utc)
    if not hive_enabled_in_db(db):
        return
    _, origin_id = _origin_for_signing(settings)
    if origin_id is None:
        return
    item = db.get(MediaItem, media_item_id)
    if item is None:
        return
    media = db.get(Media, item.media_id)
    if media is None:
        return
    exclude_all, _ = list_excluded_ruche_ids(db, media_item_id)
    if exclude_all:
        revoke_share_intent(db, media_item_id)
    elif hive_catalog_auto_share_enabled(db) or has_share_intent(db, media_item_id):
        grant_share_intent(db, media_item_id)
    ruche_ids = member_ruche_ids_for_instance(db, origin_id)
    _sync_one_media_item_hive_catalog(
        db,
        settings,
        item=item,
        media=media,
        ruche_ids=ruche_ids,
        origin_id=origin_id,
        moment=moment,
    )


def tombstone_all_local_entries(db: Session, settings: Settings, *, now: datetime) -> None:
    _, origin_id = _origin_for_signing(settings)
    if origin_id is None:
        return
    ruche_ids = member_ruche_ids_for_instance(db, origin_id)
    for rid in ruche_ids:
        items = list(
            db.scalars(
                select(MediaItem)
                .join(HiveEntry, HiveEntry.media_item_id == MediaItem.id)
                .where(
                    HiveEntry.ruche_id == rid,
                    HiveEntry.origin_instance_id == origin_id,
                )
                .distinct()
            ).all()
        )
        for item in items:
            media = db.get(Media, item.media_id)
            if media is None:
                continue
            publish_tombstone(db, settings, ruche_id=rid, media=media, item=item, now=now)


def sync_hive_catalog(db: Session, settings: Settings, *, now: datetime | None = None) -> None:
    """Met à jour empreintes, entrées disponibles ou pierres tombales, et « Garder » Hive."""
    from mediatracker.hive.identity import hive_enabled_in_db

    moment = now or datetime.now(timezone.utc)
    if not hive_enabled_in_db(db):
        return
    _, origin_id = _origin_for_signing(settings)
    if origin_id is None:
        return
    ruche_ids = member_ruche_ids_for_instance(db, origin_id)

    purge_old_tombstones(db, now=moment)
    abandon_orphan_fingerprint_work(db)

    if hive_catalog_auto_share_enabled(db):
        items = list(db.scalars(select(MediaItem)).all())
    else:
        from mediatracker.db.models import HiveShareIntent

        intent_ids = set(db.scalars(select(HiveShareIntent.media_item_id)).all())
        open_local_ids = set(
            db.scalars(
                select(HiveEntry.media_item_id).where(
                    HiveEntry.origin_instance_id == origin_id,
                    HiveEntry.etat == "disponible",
                    HiveEntry.media_item_id.is_not(None),
                )
            ).all()
        )
        keep_ids = intent_ids | open_local_ids
        if keep_ids:
            items = list(db.scalars(select(MediaItem).where(MediaItem.id.in_(keep_ids))).all())
        else:
            items = []
    for item in items:
        media = db.get(Media, item.media_id)
        if media is None or item.id is None:
            continue
        _sync_one_media_item_hive_catalog(
            db,
            settings,
            item=item,
            media=media,
            ruche_ids=ruche_ids,
            origin_id=origin_id,
            moment=moment,
        )

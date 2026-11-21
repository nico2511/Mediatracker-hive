"""Onglet Recherché : titres demandés sans possesseur acceptant, volontaires."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mediatracker.api.errors import Problem
from mediatracker.config import Settings
from mediatracker.core.follow import FollowError, FollowInput, follow
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import (
    HiveRequestResolution,
    HiveSignal,
    HiveWantedVolunteer,
    Media,
    MediaItem,
    QualityProfile,
)
from mediatracker.hive.crypto_id import hive_public_id_from_public_key
from mediatracker.hive.hive_catalogue import _stable_work_key
from mediatracker.hive.identity import load_private_key
from mediatracker.hive.membership import ruches_for_instance
from mediatracker.hive.share_policy import grant_share_intent, has_share_intent
from mediatracker.hive.signal import (
    cle_hash,
    item_file_present,
    latest_cherche_rows_for_ruche,
    parse_cle,
)
from mediatracker.hive.signal_mini_fiche import mini_fiche_display_meta, parse_stored_mini_fiche
from mediatracker.hive.wanted_tmdb import fetch_wanted_tmdb_meta

WANTED_POSSEDE_GRACE_HOURS = 72
AUTO_TAKE_WANTED_MAX_PER_PASS = 3

logger = logging.getLogger(__name__)


def _aware_utc(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _self_origin(settings: Settings) -> str | None:
    pk = load_private_key(settings)
    if pk is None:
        return None
    return hive_public_id_from_public_key(pk.public_key())


def media_type_from_cle(cle: dict) -> str:
    raw = str(cle.get("type") or "film")
    return "film" if raw in {"film", "movie"} else "series"


def _media_type_from_cle(cle: dict) -> str:
    return media_type_from_cle(cle)


def _work_key_for_cle(cle: dict) -> str:
    normalized = dict(cle)
    normalized["type"] = _media_type_from_cle(cle)
    return _stable_work_key(normalized)


def _local_possesses_cle(db: Session, cle: dict) -> bool:
    tmdb_id = cle.get("tmdb_id")
    media_type = _media_type_from_cle(cle)
    if tmdb_id is None:
        return False
    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is None:
        return False
    if media_type == "series":
        season, episode = cle.get("saison"), cle.get("episode")
        if season is None or episode is None:
            return False
        item = db.scalar(
            select(MediaItem).where(
                MediaItem.media_id == media.id,
                MediaItem.season == season,
                MediaItem.episode == episode,
            ).limit(1)
        )
    else:
        item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
    return item is not None and item_file_present(db, item)


def _self_cherche_for_work(
    db: Session, *, ruche_id: bytes, work_key: str, self_origin: str | None
) -> bool:
    if self_origin is None:
        return False
    for row in latest_cherche_rows_for_ruche(db, ruche_id=ruche_id):
        if row.origin_instance_id != self_origin:
            continue
        cle = parse_cle(row.cle_json)
        if cle and _work_key_for_cle(cle) == work_key:
            return True
    return False


def _possede_active_for_work(db: Session, *, ruche_id: bytes, work_key: str) -> bool:
    rows = list(
        db.scalars(
            select(HiveSignal).where(HiveSignal.ruche_id == ruche_id, HiveSignal.signal == "possede")
        ).all()
    )
    for row in rows:
        cle = parse_cle(row.cle_json)
        if cle and _work_key_for_cle(cle) == work_key:
            return True
    return False


def _first_cherche_at_for_work(db: Session, *, ruche_id: bytes, work_key: str) -> datetime | None:
    earliest: datetime | None = None
    for row in latest_cherche_rows_for_ruche(db, ruche_id=ruche_id):
        cle = parse_cle(row.cle_json)
        if not cle or _work_key_for_cle(cle) != work_key:
            continue
        created = _aware_utc(row.created_at)
        if earliest is None or created < earliest:
            earliest = created
    return earliest


# Les acceptations HiveRequestResolution sont locales au possesseur : les autres
# membres ne les voient pas via le gossip. Le délai WANTED_POSSEDE_GRACE_HOURS
# évite d'afficher trop tôt un titre quand un possède existe sans acceptation connue.
def _accepted_transfer_for_work(db: Session, *, ruche_id: bytes, work_key: str) -> bool:
    for row in latest_cherche_rows_for_ruche(db, ruche_id=ruche_id):
        cle = parse_cle(row.cle_json)
        if not cle or _work_key_for_cle(cle) != work_key:
            continue
        target_hash = row.cle_hash or cle_hash(cle)
        accepted = db.scalar(
            select(HiveRequestResolution.id)
            .where(
                HiveRequestResolution.ruche_id == ruche_id,
                HiveRequestResolution.cle_hash == target_hash,
                HiveRequestResolution.status == "accepted",
            )
            .limit(1)
        )
        if accepted is not None:
            return True
    return False


def _wanted_visible(
    db: Session,
    *,
    ruche_id: bytes,
    work_key: str,
    now: datetime,
    self_origin: str | None,
) -> bool:
    if _accepted_transfer_for_work(db, ruche_id=ruche_id, work_key=work_key):
        return False
    first_at = _first_cherche_at_for_work(db, ruche_id=ruche_id, work_key=work_key)
    if first_at is None:
        return False
    has_possede = _possede_active_for_work(db, ruche_id=ruche_id, work_key=work_key)
    if not has_possede:
        return True
    grace = timedelta(hours=WANTED_POSSEDE_GRACE_HOURS)
    return _aware_utc(now) >= first_at + grace


def _aggregate_cherche_by_work(
    db: Session, *, ruche_id: bytes, self_origin: str | None
) -> dict[str, tuple[dict, int, dict | None]]:
    """work_key → (représentatif cle, nombre de demandeurs, mini-fiche signal si présente)."""
    counts: dict[str, tuple[dict, set[str]]] = {}
    mini_by_work: dict[str, dict] = {}
    for row in latest_cherche_rows_for_ruche(db, ruche_id=ruche_id):
        origin = row.origin_instance_id
        if not origin or (self_origin and origin == self_origin):
            continue
        cle = parse_cle(row.cle_json)
        if not cle:
            continue
        wk = _work_key_for_cle(cle)
        bucket = counts.setdefault(wk, (cle, set()))
        bucket[1].add(origin)
        counts[wk] = (cle, bucket[1])
        if wk not in mini_by_work:
            mini = parse_stored_mini_fiche(row.mini_fiche_json)
            if mini:
                mini_by_work[wk] = mini
    return {
        wk: (cle, len(origins), mini_by_work.get(wk))
        for wk, (cle, origins) in counts.items()
    }


def _resolve_wanted_display_meta(
    db: Session,
    settings: Settings,
    *,
    cle: dict,
    mini_fiche: dict | None,
    moment: datetime,
) -> dict[str, Any]:
    media_type = _media_type_from_cle(cle)
    if mini_fiche:
        return mini_fiche_display_meta(mini_fiche, media_type=media_type)
    tmdb_id = cle.get("tmdb_id")
    if isinstance(tmdb_id, int):
        media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
        if media is not None and (media.title or "").strip():
            return {
                "title": media.title.strip(),
                "year": media.year,
                "poster_url": media.poster_url,
            }
        return fetch_wanted_tmdb_meta(db, settings, media_type=media_type, tmdb_id=tmdb_id, now=moment)
    work_key = _work_key_for_cle(cle)
    return {"title": f"Fiche TMDB {work_key[:8]}", "year": None, "poster_url": None}


def list_wanted_requests(db: Session, settings: Settings, *, now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    self_origin = _self_origin(settings)
    if self_origin is None:
        return []
    out: dict[str, dict[str, Any]] = {}
    for ruche in ruches_for_instance(db, self_origin):
        rid = ruche.ruche_id
        work_map = _aggregate_cherche_by_work(db, ruche_id=rid, self_origin=self_origin)
        for work_key, (cle, requester_count, mini_fiche) in work_map.items():
            if requester_count < 1:
                continue
            if _self_cherche_for_work(db, ruche_id=rid, work_key=work_key, self_origin=self_origin):
                continue
            if _local_possesses_cle(db, cle):
                continue
            if not _wanted_visible(db, ruche_id=rid, work_key=work_key, now=moment, self_origin=self_origin):
                continue
            volunteering = db.scalar(
                select(HiveWantedVolunteer.id)
                .where(
                    HiveWantedVolunteer.ruche_id == rid,
                    HiveWantedVolunteer.work_key == work_key,
                    HiveWantedVolunteer.volunteer_origin == self_origin,
                )
                .limit(1)
            )
            volunteering = volunteering is not None
            prev = out.get(work_key)
            if prev is not None:
                prev["requester_count"] += requester_count
                prev["volunteering"] = prev["volunteering"] or volunteering
                continue
            media_type = _media_type_from_cle(cle)
            tmdb_id = cle.get("tmdb_id")
            meta = _resolve_wanted_display_meta(
                db, settings, cle=cle, mini_fiche=mini_fiche, moment=moment
            )
            season = cle.get("saison") if media_type == "series" else None
            episode = cle.get("episode") if media_type == "series" else None
            out[work_key] = {
                "work_key": work_key,
                "title": meta["title"],
                "year": meta.get("year"),
                "type": media_type,
                "tmdb_id": tmdb_id,
                "season": season,
                "episode": episode,
                "poster_url": meta.get("poster_url"),
                "requester_count": requester_count,
                "volunteering": volunteering,
            }
    items = list(out.values())
    items.sort(key=lambda row: (-int(row["requester_count"]), row["title"]))
    return items


def _resolve_work_cle(db: Session, settings: Settings, work_key: str) -> tuple[bytes, dict]:
    self_origin = _self_origin(settings)
    if self_origin is None:
        raise Problem(422, "Requête invalide", "Identité Hive absente.")
    for ruche in ruches_for_instance(db, self_origin):
        rid = ruche.ruche_id
        for row in latest_cherche_rows_for_ruche(db, ruche_id=rid):
            cle = parse_cle(row.cle_json)
            if cle and _work_key_for_cle(cle) == work_key:
                return rid, cle
    raise Problem(404, "Introuvable", "Titre absent de la liste Recherché.")


def _slot_for_cle(db: Session, media: Media, cle: dict) -> MediaItem:
    media_type = _media_type_from_cle(cle)
    if media_type == "series":
        season, episode = cle.get("saison"), cle.get("episode")
        if season is None or episode is None:
            raise Problem(422, "Requête invalide", "Créneau série incomplet.")
        item = db.scalar(
            select(MediaItem).where(
                MediaItem.media_id == media.id,
                MediaItem.season == season,
                MediaItem.episode == episode,
            ).limit(1)
        )
        if item is None:
            raise Problem(500, "Erreur interne", "Épisode introuvable après suivi.")
        return item
    item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
    if item is None:
        item = MediaItem(media_id=media.id, season=None, episode=None)
        db.add(item)
        db.flush()
    return item


def _follow_for_wanted(
    db: Session, settings: Settings, *, cle: dict, now: datetime
) -> tuple[Media, MediaItem]:
    media_type = _media_type_from_cle(cle)
    tmdb_id = cle.get("tmdb_id")
    if not isinstance(tmdb_id, int):
        raise Problem(422, "Requête invalide", "Identifiant TMDB absent.")
    existing = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    body = FollowInput(
        type=media_type,
        tmdb_id=tmdb_id,
        start_now=True,
        seasons=[int(cle["saison"])] if media_type == "series" and cle.get("saison") is not None else None,
    )
    if not tmdb_token.read_tmdb_token(db, settings.secret_key):
        meta = fetch_wanted_tmdb_meta(db, settings, media_type=media_type, tmdb_id=tmdb_id, now=now)
        body.title = meta["title"]
        body.year = meta.get("year")
        body.poster_url = meta.get("poster_url")
        body.overview = meta.get("overview")
    try:
        if existing is not None and existing.followed:
            media = existing
            item = _slot_for_cle(db, media, cle)
            if not item_file_present(db, item):
                existing.download_backlog = True
                existing.catch_up_pending = True
                existing.next_source_poll_at = now
        elif existing is not None:
            media, items = follow(db, settings.secret_key, body, media=existing, source="existing")
            item = _slot_for_cle(db, media, cle)
        else:
            media, items = follow(db, settings.secret_key, body, source="create")
            item = _slot_for_cle(db, media, cle)
    except FollowError as exc:
        raise Problem(exc.status, exc.title, exc.detail) from exc
    if item.id is None:
        raise Problem(500, "Erreur interne", "Fiche introuvable.")
    return media, item


def take_wanted_request(
    db: Session,
    settings: Settings,
    *,
    work_key: str,
    release: bool,
    now: datetime,
) -> None:
    self_origin = _self_origin(settings)
    if self_origin is None:
        raise Problem(422, "Requête invalide", "Identité Hive absente.")
    rid, cle = _resolve_work_cle(db, settings, work_key)
    if release:
        db.execute(
            delete(HiveWantedVolunteer).where(
                HiveWantedVolunteer.ruche_id == rid,
                HiveWantedVolunteer.work_key == work_key,
                HiveWantedVolunteer.volunteer_origin == self_origin,
            )
        )
        return
    wanted_now = list_wanted_requests(db, settings, now=now)
    if not any(row["work_key"] == work_key for row in wanted_now):
        raise Problem(404, "Introuvable", "Titre absent de la liste Recherché.")
    existing = db.scalar(
        select(HiveWantedVolunteer).where(
            HiveWantedVolunteer.ruche_id == rid,
            HiveWantedVolunteer.work_key == work_key,
            HiveWantedVolunteer.volunteer_origin == self_origin,
        ).limit(1)
    )
    if existing is not None:
        return
    media, item = _follow_for_wanted(db, settings, cle=cle, now=now)
    db.add(
        HiveWantedVolunteer(
            ruche_id=rid,
            work_key=work_key,
            volunteer_origin=self_origin,
            media_item_id=item.id,
            created_at=now,
        )
    )
    db.flush()


def media_has_wanted_volunteer(db: Session, media_id: int) -> bool:
    row = db.scalar(
        select(HiveWantedVolunteer.id)
        .join(MediaItem, MediaItem.id == HiveWantedVolunteer.media_item_id)
        .where(MediaItem.media_id == media_id)
        .limit(1)
    )
    return row is not None


def volunteer_poll_profile_chain(db: Session):
    """Profil par défaut de l’instance seul (bon samaritain Recherché)."""
    from mediatracker.core.profile_attempt_orchestration import ProfileChain
    from mediatracker.core.quality_profile import profile_from_row

    row = db.scalar(select(QualityProfile).where(QualityProfile.is_default.is_(True)).limit(1))
    if row is None:
        return None
    profile = profile_from_row(row)
    if profile.quality_profile_id is None:
        return None
    return ProfileChain(profiles=(profile,))


def hive_volunteer_active_for_item(db: Session, media_item_id: int) -> bool:
    row = db.scalar(
        select(HiveWantedVolunteer.id).where(HiveWantedVolunteer.media_item_id == media_item_id).limit(1)
    )
    return row is not None


def _coarse_work_identity(media_type: str, tmdb_id: Any) -> tuple[str, int] | None:
    if not isinstance(tmdb_id, int):
        return None
    return (media_type, tmdb_id)


def _auto_take_skip_local_busy(db: Session, row: dict[str, Any]) -> bool:
    """Ignore déjà volontaire, fiche suivie localement ou téléchargement en cours."""
    if row.get("volunteering"):
        return True
    tmdb_id = row.get("tmdb_id")
    media_type = str(row.get("type") or "film")
    if not isinstance(tmdb_id, int):
        return False
    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is None:
        return False
    if media.followed:
        return True
    from mediatracker.core.pipeline.download_guard import active_download_for_item

    items_q = select(MediaItem).where(MediaItem.media_id == media.id)
    if media_type == "series":
        season, episode = row.get("season"), row.get("episode")
        if season is not None and episode is not None:
            items_q = items_q.where(MediaItem.season == season, MediaItem.episode == episode)
    for item in db.scalars(items_q).all():
        if item.id is None:
            continue
        if item_file_present(db, item):
            return True
        if active_download_for_item(db, item.id) is not None:
            return True
    if bool(getattr(media, "download_backlog", False)) or bool(media.catch_up_pending):
        return True
    return False


def try_auto_take_wanted_for_instance(
    db: Session,
    settings: Settings,
    *,
    now: datetime,
    limit: int = AUTO_TAKE_WANTED_MAX_PER_PASS,
) -> int:
    from mediatracker.hive.identity import hive_enabled_in_db
    from mediatracker.hive.signal_policy import hive_auto_take_wanted_enabled

    if not hive_enabled_in_db(db) or not hive_auto_take_wanted_enabled(db):
        return 0
    rows = list_wanted_requests(db, settings, now=now)
    taken = 0
    seen_coarse: set[tuple[str, int]] = set()
    for row in rows:
        if taken >= limit:
            break
        if _auto_take_skip_local_busy(db, row):
            continue
        coarse = _coarse_work_identity(str(row.get("type") or "film"), row.get("tmdb_id"))
        if coarse is not None and coarse in seen_coarse:
            continue
        try:
            take_wanted_request(
                db,
                settings,
                work_key=str(row["work_key"]),
                release=False,
                now=now,
            )
        except Problem:
            logger.debug("Auto-prise recherché ignorée (%s).", row.get("work_key", "")[:8])
            continue
        except Exception:
            logger.exception(
                "Auto-prise recherché échouée (work_key=%s).", row.get("work_key", "")[:8]
            )
            continue
        taken += 1
        if coarse is not None:
            seen_coarse.add(coarse)
    return taken


def maybe_try_auto_take_wanted(db: Session, settings: Settings, *, now: datetime) -> int:
    """Prise en charge automatique des recherches « Personne ne l’a encore »."""
    return try_auto_take_wanted_for_instance(db, settings, now=now)


def complete_wanted_volunteer_share(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
) -> bool:
    del settings, media, now
    if item.id is None or not item_file_present(db, item):
        return False
    row = db.scalar(select(HiveWantedVolunteer).where(HiveWantedVolunteer.media_item_id == item.id).limit(1))
    if row is None:
        return False
    if not has_share_intent(db, item.id):
        grant_share_intent(db, item.id)
    db.execute(delete(HiveWantedVolunteer).where(HiveWantedVolunteer.id == row.id))
    return True

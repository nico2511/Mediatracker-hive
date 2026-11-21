"""Raccord Suivre → catalogue Hive fusionné → file de transfert T5."""

from __future__ import annotations

import random
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Download, Media, MediaItem
from mediatracker.hive.download_aria2 import enqueue_hive_download_match, hive_reception_active_for_item
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.source_catalog import HiveCatalogMatch


def enqueue_hive_match(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    match: HiveCatalogMatch,
    *,
    now: datetime | None = None,
) -> Download | None:
    moment = now or datetime.now(timezone.utc)
    return enqueue_hive_download_match(db, settings, media, item, match, now=moment)


def run_hive_follow_poll(
    db: Session,
    media: Media,
    settings: Settings,
    *,
    now: datetime | None = None,
    force: bool = False,
    rng: random.Random | None = None,
    commit: bool = True,
    single_profile=None,
    schedule_finish: bool = True,
):
    """Contrôle Hive pour les épisodes manquants. Retourne un PollReport ou None si hors périmètre."""
    from mediatracker.core.source_poll import (
        PollReport,
        _finish,
        _gate,
        expected_slots,
        poll_found_message,
        queues_download,
    )

    if not hive_enabled_in_db(db):
        return None
    moment = now or datetime.now(timezone.utc)
    draw = rng or random.Random()
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    if not force:
        allowed, _reason = _gate(db, settings, media, items, moment)
        if not allowed:
            return PollReport(
                result="skipped",
                message="",
                episodes=0,
                queued=0,
                proposed=0,
                last_check_at=media.last_source_poll_at or moment,
                next_check_at=media.next_source_poll_at or moment,
            )
    from mediatracker.core.source_poll import _setting_int

    window = _setting_int(db, "scan_air_window_days", settings.scan_air_window_days)
    backlog = bool(getattr(media, "download_backlog", False)) and not media.on_hold
    catching = backlog or (bool(media.catch_up_pending) and media.new_episode_policy == "auto" and not media.on_hold)
    slots = expected_slots(media, items, today=moment.date(), window_days=window, force=force, catch_up=catching)
    if not slots:
        no_slots = (
            "Aucun film attendu pour le moment."
            if media.type == "film"
            else "Aucun épisode attendu pour le moment."
        )
        return _finish(
            db,
            media,
            settings,
            moment,
            draw,
            result="nothing",
            message=no_slots,
            episodes=0,
            queued=0,
            proposed=0,
            failed=False,
            force=force,
            schedule=schedule_finish,
        )
    from mediatracker.core.profile_attempt_orchestration import (
        pick_hive_catalog_for_item,
        profiles_for_poll_attempt,
    )

    chain, resolve_error = profiles_for_poll_attempt(db, media, settings, single_profile=single_profile)
    if resolve_error:
        return _finish(
            db,
            media,
            settings,
            moment,
            draw,
            result="error",
            message=resolve_error,
            episodes=0,
            queued=0,
            proposed=0,
            failed=True,
            force=force,
            schedule=schedule_finish,
        )
    assert chain is not None
    by_item = {item.id: item for item in items if item.id is not None}
    queued = proposed = episodes = 0
    had_pending = False
    for slot in slots:
        if slot.item_id is None:
            continue
        item = by_item.get(slot.item_id)
        if item is None:
            continue
        if item.watched or item.downloaded or item.file_path:
            continue
        pick = pick_hive_catalog_for_item(
            db,
            media,
            item,
            chain,
            prefer_available=True,
            single_profile=single_profile,
        )
        if pick is None:
            continue
        match = pick.match
        if match is None:
            continue
        episodes += 1
        if hive_reception_active_for_item(db, item, match.sha256_hex, settings):
            continue
        if not match.row.disponible and not any(s.get("joignable") for s in match.sources):
            had_pending = True
            proposed += 1
            continue
        if match.expected_size <= 0:
            had_pending = True
            proposed += 1
            continue
        if queues_download(media.new_episode_policy, bool(getattr(media, "download_backlog", False))):
            if enqueue_hive_match(db, settings, media, item, match, now=moment) is None:
                had_pending = True
                proposed += 1
                continue
            queued += 1
        else:
            proposed += 1
    if episodes == 0:
        return None
    if queued > 0:
        if media.type == "film":
            message = poll_found_message(media, episodes=episodes, queued=queued, auto=True)
        else:
            message = f"{episodes} épisode(s) trouvé(s), {queued} téléchargement(s) Hive en file."
        result = "found"
    elif had_pending:
        message = "Version connue mais indisponible pour l'instant dans la ruche."
        result = "nothing"
    elif media.type == "film":
        message = poll_found_message(media, episodes=episodes, queued=0, auto=False)
        result = "found"
    else:
        message = f"{episodes} épisode(s) repéré(s), en attente de confirmation."
        result = "found"
    report = _finish(
        db,
        media,
        settings,
        moment,
        draw,
        result=result,
        message=message,
        episodes=episodes,
        queued=queued,
        proposed=proposed,
        failed=False,
        force=force,
        schedule=schedule_finish,
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return report


def poll_follow_for_media_id(
    factory,
    settings: Settings,
    media_id: int,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
):
    """Profil d'abord puis ordre utilisateur des sources (lot E, contrat PR181)."""
    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll

    moment = now or datetime.now(timezone.utc)
    draw = rng or random.Random()
    return orchestrate_profile_first_poll(factory, settings, media_id, now=moment, rng=draw, force=False)


def poll_follow_respecting_order(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
    commit: bool = True,
):
    from sqlalchemy.orm import sessionmaker

    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
    from mediatracker.core.source_poll import next_due

    moment = now or datetime.now(timezone.utc)
    media = next_due(db, settings, moment, commit=commit)
    if media is None:
        return None
    draw = rng or random.Random()
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    return orchestrate_profile_first_poll(factory, settings, media.id, now=moment, rng=draw, force=False)

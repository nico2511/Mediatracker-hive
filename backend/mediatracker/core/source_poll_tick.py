"""Worker source poll: prepare (commit) / collect (no session) / apply (fresh session, bounded retry)."""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from mediatracker.config import Settings
from mediatracker.core.source_poll import next_due
from mediatracker.core.source_poll_phased import worker_remote_poll
from mediatracker.db.session import commit_retry
from mediatracker.hive.source_poll import poll_follow_for_media_id

logger = logging.getLogger(__name__)


def prepare_due_media(factory: sessionmaker[Session], settings: Settings, *, now: datetime | None = None) -> int | None:
    """Phase 1: retire obtained films (with journal) and commit before any network."""
    moment = now or datetime.now(timezone.utc)
    db = factory()
    try:
        media = next_due(db, settings, moment, commit=True)
        if media is None:
            return None
        media_id = media.id
        commit_retry(db)
        return media_id
    finally:
        db.close()


def worker_poll_if_due(
    factory: sessionmaker[Session],
    settings: Settings,
    *,
    now: datetime | None = None,
    rng: random.Random | None = None,
    sources: list[tuple[int, object]] | None = None,
):
    """Production worker entry: one due media per tick, no shared SAVEPOINT transaction."""
    moment = now or datetime.now(timezone.utc)
    draw = rng or random.Random()
    media_id = prepare_due_media(factory, settings, now=moment)
    if media_id is None:
        return None
    if sources is not None:
        return _poll_injected_sources(factory, settings, media_id, sources, moment, draw)
    return poll_follow_for_media_id(factory, settings, media_id, now=moment, rng=draw)


def _poll_injected_sources(
    factory: sessionmaker[Session],
    settings: Settings,
    media_id: int,
    sources: list[tuple[int, object]],
    moment: datetime,
    draw: random.Random,
):
    from mediatracker.core.source_poll import close_source

    if not sources:
        return None
    provider_id, source = sources[0]
    try:
        return worker_remote_poll(
            factory,
            settings,
            media_id,
            provider_id,
            source,
            moment,
            draw,
        )
    finally:
        for _pid, opened in sources:
            close_source(opened)


def pump_forced_source_check_worker(factory: sessionmaker[Session], settings: Settings) -> bool:
    """Forced check wave with isolated sessions (same boundaries as poll_if_due)."""
    from mediatracker.core.source_force import (
        _advance_past_wave,
        _next_media,
        _put,
        _since,
    )
    db = factory()
    try:
        since = _since(db)
        if since is None:
            return False
        media = _next_media(db, since)
        if media is None:
            _put(db, "system.force_source_check_at", "", datetime.now(timezone.utc))
            commit_retry(db)
            return False
        if not media.followed:
            return False
        media_id = media.id
        wave_since = since
    finally:
        db.close()

    from mediatracker.core.profile_attempt_orchestration import orchestrate_profile_first_poll
    from mediatracker.db.models import Media

    moment = datetime.now(timezone.utc)
    draw = random.Random()

    orchestrate_profile_first_poll(factory, settings, media_id, force=True, now=moment, rng=draw)

    def _advance(db: Session):
        media = db.get(Media, media_id)
        if media is None or not media.followed:
            return False
        since = _since(db)
        if since is None or since != wave_since:
            return False
        _advance_past_wave(db, media, wave_since, settings, commit=False)
        return True

    from mediatracker.db.session import apply_with_retry

    apply_with_retry(factory, "Avancement vague forcée", _advance)
    return True

"""Politique de publication cherche (AllDebrid, hive_request_on_miss)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Media, MediaItem, Setting
from mediatracker.hive.signal import (
    item_file_present,
    publish_cherche_for_item,
    retract_cherche_for_item,
)
from mediatracker.hive.signal_cherche_retract import (
    cherche_retract_active,
    clear_cherche_retract,
    set_cherche_retract,
)
from mediatracker.hive.signal_miss_prompt import clear_cherche_miss_prompt, set_cherche_miss_prompt
from mediatracker.integrations.alldebrid_key import effective_alldebrid_key

HIVE_REQUEST_ON_MISS_KEY = "hive_request_on_miss"
HIVE_AUTO_ACCEPT_REQUESTS_KEY = "hive_auto_accept_requests"
HIVE_AUTO_TAKE_WANTED_KEY = "hive_auto_take_wanted"
CHERCHE_CATCHUP_AT_KEY = "hive.cherche_follow_catchup_at"
CHERCHE_CATCHUP_INTERVAL = timedelta(minutes=5)

_VALID_ON_MISS = frozenset({"auto", "ask", "never"})


def hive_request_on_miss_mode(db: Session) -> str:
    row = db.get(Setting, HIVE_REQUEST_ON_MISS_KEY)
    raw = (row.value if row and row.value else "auto").strip().lower()
    return raw if raw in _VALID_ON_MISS else "auto"


def hive_auto_accept_requests_enabled(db: Session) -> bool:
    from mediatracker.hive.transfer_settings import _read_bool

    return _read_bool(db, HIVE_AUTO_ACCEPT_REQUESTS_KEY, default=False)


def hive_auto_take_wanted_enabled(db: Session) -> bool:
    from mediatracker.hive.transfer_settings import _read_bool

    return _read_bool(db, HIVE_AUTO_TAKE_WANTED_KEY, default=False)


def alldebrid_configured(settings: Settings, db: Session) -> bool:
    return bool(effective_alldebrid_key(settings, db))


def should_publish_cherche_on_follow(settings: Settings, db: Session) -> bool:
    """Sans clé AllDebrid uniquement : le suivi déclenche l’annonce cherche."""
    return not alldebrid_configured(settings, db)


def should_publish_cherche_on_miss(settings: Settings, db: Session) -> bool:
    """Avec clé AllDebrid : publication après échec de recherche, si mode auto."""
    if not alldebrid_configured(settings, db):
        return False
    return hive_request_on_miss_mode(db) == "auto"


def should_record_miss_prompt(settings: Settings, db: Session) -> bool:
    if not alldebrid_configured(settings, db):
        return False
    return hive_request_on_miss_mode(db) == "ask"


def hive_volunteer_suppresses_cherche(db: Session, media_item_id: int | None) -> bool:
    if media_item_id is None:
        return False
    from mediatracker.hive.signal_wanted import hive_volunteer_active_for_item

    return hive_volunteer_active_for_item(db, media_item_id)


def _parse_catchup_at(db: Session) -> datetime | None:
    row = db.get(Setting, CHERCHE_CATCHUP_AT_KEY)
    if row is None or not row.value:
        return None
    try:
        parsed = datetime.fromisoformat(row.value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _touch_catchup_at(db: Session, when: datetime) -> None:
    row = db.get(Setting, CHERCHE_CATCHUP_AT_KEY)
    text = when.isoformat()
    if row is None:
        db.add(Setting(key=CHERCHE_CATCHUP_AT_KEY, value=text))
    else:
        row.value = text


def _catchup_due(db: Session, now: datetime) -> bool:
    last = _parse_catchup_at(db)
    if last is None:
        return True
    return now - last >= CHERCHE_CATCHUP_INTERVAL


def _cherche_moment_day(now: datetime) -> date:
    if now.tzinfo is None:
        return now.date()
    return now.astimezone(timezone.utc).date()


def episode_aired_for_cherche(item: MediaItem, *, today: date) -> bool:
    """Série : pas d’annonce avant diffusion TMDB (date connue et déjà passée)."""
    when = item.air_date
    if when is None:
        return False
    return when <= today


def cherche_slot_eligible_for_publish(
    media: Media,
    item: MediaItem,
    *,
    now: datetime,
) -> bool:
    """Filtre commun suivi + rattrapage : pause fiche, épisode pas encore diffusé."""
    if media.on_hold:
        return False
    today = _cherche_moment_day(now)
    if media.type == "series":
        return episode_aired_for_cherche(item, today=today)
    if media.type == "film":
        from mediatracker.core.film_availability import film_not_yet_available

        if film_not_yet_available(media, episodes=[item], today=today):
            return False
    return True


def items_for_cherche_on_follow(
    db: Session,
    media: Media,
    hinted: list[MediaItem] | None = None,
    *,
    now: datetime | None = None,
) -> list[MediaItem]:
    """Épisodes manquants connus (série) ou créneau film après un suivi."""
    from mediatracker.hive.signal import item_file_present

    if media.on_hold:
        return []
    moment = now or datetime.now(timezone.utc)
    pool = list(hinted or [])
    if not pool:
        pool = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    if media.type == "film":
        eligible = [row for row in pool if row.id is not None]
        return eligible[:1]
    today = _cherche_moment_day(moment)
    return [
        row
        for row in pool
        if not item_file_present(db, row)
        and not row.watched
        and episode_aired_for_cherche(row, today=today)
    ]


def publish_cherche_after_follow(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    items: list[MediaItem] | None = None,
    now: datetime,
) -> None:
    """Après suivi réussi : annonce cherche sur les créneaux éligibles (sans clé AllDebrid)."""
    from mediatracker.hive.identity import hive_enabled_in_db

    if not hive_enabled_in_db(db) or not media.followed or media.on_hold:
        return
    for item in items_for_cherche_on_follow(db, media, items, now=now):
        maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=now)


def maybe_catchup_cherche_for_followed(
    db: Session,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> bool:
    """Rattrapage idempotent : retrait créneaux indisponibles ; publication si pas de clé AllDebrid."""
    from mediatracker.hive.identity import hive_enabled_in_db

    moment = now or datetime.now(timezone.utc)
    if not hive_enabled_in_db(db):
        return False
    if not _catchup_due(db, moment):
        return False
    publish_on_follow = should_publish_cherche_on_follow(settings, db)
    rows = db.execute(
        select(MediaItem, Media)
        .join(Media, Media.id == MediaItem.media_id)
        .where(Media.followed.is_(True))
    ).all()
    touched = False
    for item, media in rows:
        if item.id is None:
            continue
        if not cherche_slot_eligible_for_publish(media, item, now=moment):
            if not item_file_present(db, item) and not item.watched:
                retract_cherche_for_item(db, settings, media=media, item=item, now=moment)
                clear_cherche_miss_prompt(db, item.id)
            touched = True
            continue
        if publish_on_follow:
            maybe_publish_cherche_on_follow(db, settings, media=media, item=item, now=moment)
            touched = True
    from mediatracker.hive.signal_requests import _media_item_for_cherche_cle, iter_local_active_cherche_cle

    for cle in iter_local_active_cherche_cle(db, settings):
        resolved = _media_item_for_cherche_cle(db, cle)
        if resolved is None:
            continue
        media, item = resolved
        if item.id is None:
            continue
        if not (item_file_present(db, item) or item.watched):
            continue
        retract_cherche_for_item(db, settings, media=media, item=item, now=moment)
        clear_cherche_miss_prompt(db, item.id)
        clear_cherche_retract(db, item.id)
        touched = True
    _touch_catchup_at(db, moment)
    return touched


def maybe_publish_cherche_on_follow(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now,
) -> None:
    if item.id is not None and hive_volunteer_suppresses_cherche(db, item.id):
        return
    if not should_publish_cherche_on_follow(settings, db):
        return
    if not cherche_slot_eligible_for_publish(media, item, now=now):
        return
    if not media.followed or item_file_present(db, item) or item.watched:
        if item.id is not None and (item_file_present(db, item) or item.watched):
            clear_cherche_retract(db, item.id)
        return
    if item.id is not None and cherche_retract_active(db, item.id):
        return
    publish_cherche_for_item(db, settings, media=media, item=item, now=now)


def maybe_publish_cherche_on_source_miss(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    now,
) -> None:
    if not media.followed:
        return
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    for item in items:
        if item_file_present(db, item) or item.watched:
            if item.id is not None:
                clear_cherche_miss_prompt(db, item.id)
                clear_cherche_retract(db, item.id)
            continue
        if item.id is not None and cherche_retract_active(db, item.id):
            continue
        if item.id is not None and hive_volunteer_suppresses_cherche(db, item.id):
            continue
        if not cherche_slot_eligible_for_publish(media, item, now=now):
            continue
        if should_publish_cherche_on_miss(settings, db):
            publish_cherche_for_item(db, settings, media=media, item=item, now=now)
            if item.id is not None:
                clear_cherche_miss_prompt(db, item.id)
        elif should_record_miss_prompt(settings, db) and item.id is not None:
            set_cherche_miss_prompt(db, item.id, now=now)


def explicit_announce_cherche(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now,
) -> bool:
    """Annonce explicite : efface le retrait et publie, quel que soit hive_request_on_miss."""
    if item.id is None or not media.followed:
        return False
    if item_file_present(db, item) or item.watched:
        return False
    if not cherche_slot_eligible_for_publish(media, item, now=now):
        return False
    clear_cherche_retract(db, item.id)
    clear_cherche_miss_prompt(db, item.id)
    return publish_cherche_for_item(db, settings, media=media, item=item, now=now)


def retract_cherche_follow_item(
    db: Session,
    settings: Settings,
    *,
    media: Media,
    item: MediaItem,
    now: datetime,
    remember_user_withdraw: bool = False,
) -> None:
    """Retire le signal cherche ; remember_user_withdraw mémorise le retrait volontaire."""
    retract_cherche_for_item(db, settings, media=media, item=item, now=now)
    if item.id is not None:
        clear_cherche_miss_prompt(db, item.id)
        if remember_user_withdraw:
            set_cherche_retract(db, item.id, now=now)
        else:
            clear_cherche_retract(db, item.id)
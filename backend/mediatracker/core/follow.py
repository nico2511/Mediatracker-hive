"""Unique follow and unfollow paths for media fiches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.selection import LANGUAGES, QUALITIES
from mediatracker.core.tmdb import token as tmdb_token
from mediatracker.db.models import Download, Media, MediaItem, Setting
from mediatracker.events import record_event
from mediatracker.integrations.tmdb import TmdbError, TmdbTitle, borrow_tmdb, parse_tmdb_url

POLICIES = frozenset({"auto", "propose", "off"})
SERIES_STATUSES = frozenset({"returning", "ended", "canceled", "planned", "in_production", "unknown"})
FollowSource = Literal["create", "existing", "folder", "jellyfin_import_refollow", "patch"]


class FollowError(Exception):
    def __init__(self, status: int, title: str, detail: str) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        super().__init__(detail)


@dataclass
class FollowInput:
    type: str | None = None
    title: str | None = None
    year: int | None = None
    tmdb_id: int | None = None
    tmdb_url: str | None = None
    overview: str | None = None
    poster_url: str | None = None
    series_status: str | None = None
    on_hold: bool = False
    new_episode_policy: str | None = None
    pref_quality: str | None = None
    pref_language: str | None = None
    next_episode_season: int | None = None
    next_episode_number: int | None = None
    next_episode_title: str | None = None
    next_air_date: Any = None
    seasons: list[int] | None = None
    start_now: bool = False


def default_pref_quality(db: Session) -> str:
    row = db.get(Setting, "default_quality")
    if row is None or not (row.value or "").strip():
        return "HD"
    text = str(row.value).strip()
    return text if text in QUALITIES else "HD"


def default_pref_language(db: Session) -> str:
    row = db.get(Setting, "default_language")
    if row is None or not (row.value or "").strip():
        return "VFF"
    text = str(row.value).strip()
    return text if text in LANGUAGES else "VFF"


def resolve_follow_preferences(db: Session, body: FollowInput) -> tuple[str, str]:
    quality = body.pref_quality or default_pref_quality(db)
    language = body.pref_language or default_pref_language(db)
    return quality, language


def default_policy(db: Session) -> str:
    row = db.get(Setting, "auto_download_default")
    if row is None or row.value in (None, ""):
        return "auto"
    return "auto" if row.value == "true" else "propose"


def check_policy(policy: str) -> None:
    if policy not in POLICIES:
        raise FollowError(422, "Requête invalide", "Politique d'épisode inconnue.")


def check_status(media_type: str, status: str | None) -> None:
    if status is None:
        return
    if media_type != "series":
        raise FollowError(422, "Requête invalide", "Le statut de série ne s'applique pas à un film.")
    if status not in SERIES_STATUSES:
        raise FollowError(422, "Requête invalide", "Statut de série inconnu.")


def resolve_ref(body: FollowInput) -> tuple[int | None, str | None]:
    url_id = None
    url_type = None
    if body.tmdb_url:
        try:
            ref = parse_tmdb_url(body.tmdb_url)
        except TmdbError as exc:
            raise FollowError(422, "Requête invalide", exc.message) from exc
        url_id = ref.tmdb_id
        url_type = ref.media_type
    if body.tmdb_id is not None and url_id is not None and body.tmdb_id != url_id:
        raise FollowError(422, "Requête invalide", "L'identifiant TMDB et l'URL ne correspondent pas.")
    if body.type and url_type and body.type != url_type:
        raise FollowError(422, "Requête invalide", "Le type et l'URL TMDB ne correspondent pas.")
    return body.tmdb_id or url_id, url_type


def remember_follow_choices(row: Media, body: FollowInput) -> None:
    if row.type == "series" and body.seasons is not None:
        if len(body.seasons) > 41 or any(number < 0 or number > 40 for number in body.seasons):
            raise FollowError(422, "Requête invalide", "Numéro de saison invalide.")
        row.tracked_seasons = sorted(set(body.seasons))
    if body.start_now:
        row.download_backlog = True
        row.catch_up_pending = True
        row.next_source_poll_at = datetime.now(timezone.utc)
    else:
        row.download_backlog = False


def _apply_auto_catch_up(row: Media, policy: str, moment: datetime) -> None:
    if policy == "auto" and not row.on_hold:
        row.catch_up_pending = True
        row.next_source_poll_at = moment


def _follow_create(db: Session, secret_key: str, body: FollowInput) -> tuple[Media, list[MediaItem]]:
    """Create or reactivate a followed fiche (POST /media, catalog, HA)."""
    tmdb_id, url_type = resolve_ref(body)
    media_type = url_type or body.type
    if media_type not in {"film", "series"}:
        raise FollowError(422, "Requête invalide", "Indiquez le type, ou une URL TMDB.")
    check_status(media_type, body.series_status)
    policy = body.new_episode_policy or default_policy(db)
    check_policy(policy)
    pref_quality, pref_language = resolve_follow_preferences(db, body)
    moment = datetime.now(timezone.utc)
    if tmdb_id is not None:
        existing = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type))
        if existing is not None:
            if existing.followed:
                raise FollowError(409, "Conflit", "Ce média est déjà suivi.")
            existing.followed = True
            existing.on_hold = body.on_hold
            existing.new_episode_policy = policy
            existing.auto_download = policy == "auto"
            if existing.follow_origin != "jellyfin_import":
                existing.follow_origin = "user"
            remember_follow_choices(existing, body)
            _apply_auto_catch_up(existing, policy, moment)
            record_event(
                db,
                "media.followed",
                media_id=existing.id,
                payload={"title": existing.title, "tmdb_id": existing.tmdb_id},
            )
            db.commit()
            db.refresh(existing)
            items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == existing.id)).all())
            return existing, items
    token = tmdb_token.read_tmdb_token(db, secret_key)
    fetched: TmdbTitle | None = None
    imdb_id: str | None = None
    if tmdb_id is not None and token:
        from mediatracker.core.tmdb.catalog_settings import read_tmdb_catalog_settings
        from mediatracker.integrations import tmdb as tmdb_integration

        catalog = read_tmdb_catalog_settings(db)
        client, owned = borrow_tmdb(token, tmdb_integration.TmdbClient, language=catalog.display_language)
        try:
            fetched = client.fetch(tmdb_id, media_type)
            imdb_id = fetched.imdb_id
            if imdb_id is None:
                lookup = getattr(client, "external_imdb", None)
                if lookup is not None:
                    imdb_id = lookup(tmdb_id, media_type)
        except TmdbError as exc:
            status = 404 if exc.code == "NOT_FOUND" else 422 if exc.code == "UNAUTHORIZED" else 502
            raise FollowError(status, "TMDB", exc.message) from exc
        finally:
            if owned:
                client.close()
    elif not body.title:
        raise FollowError(
            422,
            "Requête invalide",
            "Sans jeton TMDB, le titre (et l'année, si vous la connaissez) est requis.",
        )
    title = fetched.title if fetched is not None else body.title
    if not title:
        raise FollowError(422, "Requête invalide", "Le titre est requis.")
    if fetched is not None and tmdb_id is not None:
        from mediatracker.core.tmdb.create import create_from_tmdb

        row, episodes = create_from_tmdb(
            db,
            media_type=media_type,
            tmdb_id=tmdb_id,
            fetched=fetched,
            now=moment,
            title=title,
            year=body.year,
            imdb_id=imdb_id,
            followed=True,
            follow_origin="user",
            on_hold=body.on_hold,
            series_status=fetched.series_status or body.series_status,
            new_episode_policy=policy,
            auto_download=policy == "auto",
            pref_quality=pref_quality,
            pref_language=pref_language,
            next_episode_season=body.next_episode_season,
            next_episode_number=body.next_episode_number,
            next_episode_title=body.next_episode_title,
            next_air_date=fetched.release_date if fetched.release_date else body.next_air_date,
        )
    else:
        row = Media(
            type=media_type,
            title=title,
            year=body.year,
            tmdb_id=tmdb_id if tmdb_id is not None else body.tmdb_id,
            overview=body.overview,
            poster_url=body.poster_url,
            followed=True,
            follow_origin="user",
            on_hold=body.on_hold,
            series_status=body.series_status,
            new_episode_policy=policy,
            auto_download=policy == "auto",
            pref_quality=pref_quality,
            pref_language=pref_language,
            next_episode_season=body.next_episode_season,
            next_episode_number=body.next_episode_number,
            next_episode_title=body.next_episode_title,
            next_air_date=body.next_air_date,
            created_at=moment,
        )
        db.add(row)
        db.flush()
        episodes = []
    remember_follow_choices(row, body)
    record_event(db, "media.followed", media_id=row.id, payload={"title": row.title, "tmdb_id": row.tmdb_id})
    db.commit()
    db.refresh(row)
    return row, episodes


def _follow_existing_fiche(db: Session, media: Media, body: FollowInput) -> tuple[Media, list[MediaItem]]:
    """Follow an existing fiche (POST /media/{id}/follow)."""
    if media.followed:
        raise FollowError(409, "Conflit", "Ce média est déjà suivi.")
    policy = body.new_episode_policy or default_policy(db)
    check_policy(policy)
    pref_quality, pref_language = resolve_follow_preferences(db, body)
    media.followed = True
    media.on_hold = body.on_hold
    media.new_episode_policy = policy
    media.auto_download = policy == "auto"
    if media.follow_origin != "jellyfin_import":
        media.follow_origin = "user"
    remember_follow_choices(media, body)
    moment = datetime.now(timezone.utc)
    _apply_auto_catch_up(media, policy, moment)
    record_event(
        db,
        "media.followed",
        media_id=media.id,
        payload={"title": media.title, "tmdb_id": media.tmdb_id},
    )
    return media, []


def _follow_folder_existing(db: Session, media: Media) -> tuple[Media, list[MediaItem]]:
    """Reactivate follow from library folder without changing policy."""
    media.followed = True
    if media.follow_origin != "jellyfin_import":
        media.follow_origin = "user"
    return media, []


def _follow_patch(db: Session, media: Media, *, moment: datetime) -> tuple[Media, list[MediaItem]]:
    """PATCH sets followed=true; catch-up when policy is auto."""
    media.followed = True
    if media.new_episode_policy == "auto" and not media.on_hold:
        media.catch_up_pending = True
        media.next_source_poll_at = moment
    return media, []


def _refollow_jellyfin_import(db: Session, media: Media, *, origin_label: str) -> tuple[Media, list[MediaItem]]:
    media.followed = True
    if media.follow_origin == "identification":
        media.follow_origin = "user"
    record_event(
        db,
        "media.followed",
        media_id=media.id,
        payload={"title": media.title, "from": origin_label},
    )
    return media, []


def _publish_hive_cherche_after_follow(
    db: Session,
    settings: Settings | None,
    media: Media,
    items: list[MediaItem],
    moment: datetime,
) -> None:
    if settings is None:
        from mediatracker.config import get_settings

        settings = get_settings()
    from mediatracker.hive.signal_policy import publish_cherche_after_follow

    publish_cherche_after_follow(db, settings, media=media, items=items, now=moment)


def follow(
    db: Session,
    secret_key: str | None,
    body: FollowInput | None = None,
    *,
    media: Media | None = None,
    source: FollowSource = "create",
    origin_label: str | None = None,
    moment: datetime | None = None,
    settings: Settings | None = None,
) -> tuple[Media, list[MediaItem]]:
    """Start following: ``create`` (new fiche), ``existing``, ``folder``, ``jellyfin_import_refollow``, or ``patch``."""
    when = moment or datetime.now(timezone.utc)
    if source == "create":
        if secret_key is None or body is None:
            raise FollowError(422, "Requête invalide", "Corps ou clé manquant.")
        row, items = _follow_create(db, secret_key, body)
    elif source == "existing":
        if media is None or body is None:
            raise FollowError(422, "Requête invalide", "Fiche ou corps manquant.")
        row, items = _follow_existing_fiche(db, media, body)
    elif source == "folder":
        if media is None:
            raise FollowError(422, "Requête invalide", "Fiche manquante.")
        row, items = _follow_folder_existing(db, media)
    elif source == "jellyfin_import_refollow":
        if media is None or origin_label is None:
            raise FollowError(422, "Requête invalide", "Fiche ou origine manquante.")
        row, items = _refollow_jellyfin_import(db, media, origin_label=origin_label)
    elif source == "patch":
        if media is None:
            raise FollowError(422, "Requête invalide", "Fiche manquante.")
        row, items = _follow_patch(db, media, moment=when)
    else:
        raise FollowError(422, "Requête invalide", "Source de suivi inconnue.")
    from mediatracker.core.media_hide import unhide_on_follow

    unhide_on_follow(row)
    _publish_hive_cherche_after_follow(db, settings, row, items, when)
    return row, items


def cancel_follow_queue(
    db: Session,
    media_id: int,
    now: datetime,
    settings,
    *,
    message: str = "Suivi arrêté.",
    code: str = "UNFOLLOWED",
) -> None:
    from mediatracker.core.pipeline import _downloader, cancel_job

    item_ids = list(db.scalars(select(MediaItem.id).where(MediaItem.media_id == media_id)).all())
    if not item_ids:
        return
    rows = list(
        db.scalars(
            select(Download).where(
                Download.media_item_id.in_(item_ids),
                Download.state.not_in(("done", "cancelled")),
            )
        ).all()
    )
    _provider, downloader = _downloader(db, settings, None)
    del _provider
    for download in rows:
        try:
            cancel_job(db, download, downloader, now=now)
        except RuntimeError:
            continue
        download.error = message
        download.error_code = code


def unfollow(
    db: Session,
    media: Media,
    *,
    reason: str,
    settings=None,
    moment: datetime | None = None,
    trash: bool = False,
    cancel_queue: bool = False,
    clear_schedule_flags: bool = False,
    emit_event: bool = True,
    event_payload: dict[str, Any] | None = None,
) -> bool:
    """Stop following a fiche. Returns False when already unfollowed or wrong type guard."""
    if not media.followed:
        return False
    when = moment or datetime.now(timezone.utc)
    media.followed = False
    if clear_schedule_flags:
        media.on_hold = False
        media.catch_up_pending = False
        media.download_backlog = False
    if cancel_queue and settings is not None:
        cancel_follow_queue(db, media.id, when, settings)
    if emit_event:
        payload = {"title": media.title}
        if event_payload:
            payload.update(event_payload)
        elif reason == "user":
            payload["trash"] = trash
        elif reason in {"downloaded", "complete", "identified"}:
            payload["reason"] = reason
            payload["keep_files"] = True
        record_event(db, "media.unfollowed", media_id=media.id, payload=payload)
    if settings is not None:
        from mediatracker.hive.identity import hive_enabled_in_db

        if hive_enabled_in_db(db):
            from sqlalchemy import select

            from mediatracker.db.models import MediaItem
            from mediatracker.hive.signal import retract_cherche_for_media
            from mediatracker.hive.signal_cherche_retract import clear_cherche_retract
            from mediatracker.hive.signal_miss_prompt import clear_cherche_miss_prompt

            retract_cherche_for_media(db, settings, media=media, now=when)
            for iid in db.scalars(select(MediaItem.id).where(MediaItem.media_id == media.id)).all():
                if iid is not None:
                    clear_cherche_miss_prompt(db, int(iid))
                    clear_cherche_retract(db, int(iid))
    return True


def unfollow_downloaded_film(
    db: Session,
    media: Media,
    *,
    reason: str,
    emit_event: bool = True,
    settings=None,
) -> bool:
    if media.type != "film":
        return False
    return unfollow(db, media, reason=reason, emit_event=emit_event, settings=settings)


def unfollow_complete_series(
    db: Session,
    media: Media,
    *,
    emit_event: bool = True,
    via: str = "watched",
    message: str | None = None,
    settings=None,
) -> bool:
    if media.type != "series":
        return False
    extra: dict[str, Any] = {
        "reason": "complete",
        "keep_files": True,
        "via": via,
        "automatic": True,
    }
    if message:
        extra["message"] = message
    return unfollow(
        db,
        media,
        reason="complete",
        emit_event=emit_event,
        event_payload=extra,
        settings=settings,
    )

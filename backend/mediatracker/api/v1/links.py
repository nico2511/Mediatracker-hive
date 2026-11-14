"""Manual links. The stored URL is the one the user pasted, never an unlocked CDN URL."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.core.pipeline.download_guard import active_download_for_item
from mediatracker.db.models import Download, Link, Media, MediaItem, Provider
from mediatracker.providers.sources.manual import ManualSource

router = APIRouter(prefix="/links", tags=["links"])


class LinkCreate(BaseModel):
    media_id: int
    url: str = Field(min_length=8, max_length=2000)
    season: int | None = Field(default=None, ge=0)
    episode: int | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=300)
    quality: str | None = None
    language: str | None = None
    start: bool = True


class LinkOut(BaseModel):
    id: int
    media_item_id: int
    url: str
    kind: str
    hoster: str | None
    quality: str | None
    language: str | None
    download_id: int | None
    needs_slot: bool = False


@router.post("", response_model=LinkOut, status_code=201)
def create_link(
    body: LinkCreate,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("write")),
) -> LinkOut:
    del _principal
    media = db.get(Media, body.media_id)
    if media is None:
        raise Problem(404, "Introuvable", "Média introuvable.")
    season, episode = _slot_for(media, body)
    provider = db.scalar(select(Provider).where(Provider.kind == "source", Provider.type == "manual"))
    if provider is None:
        raise Problem(422, "Requête invalide", "La source manuelle est absente.")
    candidate = ManualSource().detect(
        body.url,
        quality=body.quality,
        language=body.language,
        season=season,
        episode=episode,
    )
    needs_slot = media.type == "series" and (season is None or episode is None)
    item = _item(db, media, season, episode, body.title)
    if needs_slot:
        item.state = "ambiguous"
    if item.downloaded and body.start and not needs_slot:
        raise Problem(409, "Conflit", "Cet épisode est déjà dans la bibliothèque.")
    link = Link(
        media_item_id=item.id,
        provider_id=provider.id,
        kind=candidate.kind,
        url=candidate.url,
        hoster=candidate.hoster,
        quality=body.quality or media.pref_quality,
        language=body.language or media.pref_language,
        filename=candidate.filename,
        external_id=candidate.external_id,
        discovered_at=datetime.now(timezone.utc),
    )
    db.add(link)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise Problem(409, "Conflit", "Ce lien est déjà enregistré.") from exc
    download_id = None
    if body.start and not needs_slot:
        active = active_download_for_item(db, item.id)
        if active is None:
            created = Download(
                media_item_id=item.id,
                link_id=link.id,
                state="pending",
                created_at=datetime.now(timezone.utc),
            )
            db.add(created)
            db.flush()
            download_id = created.id
        else:
            download_id = active.id
    if item.state == "missing":
        item.state = "available"
        item.source_available = True
    db.commit()
    db.refresh(link)
    return LinkOut(
        id=link.id,
        media_item_id=item.id,
        url=link.url,
        kind=link.kind,
        hoster=link.hoster,
        quality=link.quality,
        language=link.language,
        download_id=download_id,
        needs_slot=needs_slot,
    )


def _slot_for(media: Media, body: LinkCreate) -> tuple[int | None, int | None]:
    if media.type != "series":
        return None, None
    if body.season is not None and body.episode is not None:
        return body.season, body.episode
    from mediatracker.library.parse import slot_from_name

    season, episode = slot_from_name(body.url)
    if season is None or episode is None:
        season, episode = slot_from_name(body.title or "")
    return season, episode


def _item(db: Session, media: Media, season: int | None, episode: int | None, title: str | None) -> MediaItem:
    rows = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    for row in rows:
        if row.season == season and row.episode == episode:
            if title and not row.title:
                row.title = title
            return row
    row = MediaItem(
        media_id=media.id,
        season=season,
        episode=episode,
        title=title,
        state="missing",
    )
    db.add(row)
    db.flush()
    return row

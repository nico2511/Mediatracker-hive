"""Proposition de remplacement repli → profil principal (détection cache, pas de sondage réseau dédié)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.core.file_status import FileStatusIndex
from mediatracker.core.pipeline.download_guard import active_download_for_item
from mediatracker.core.profile_attempt_orchestration import (
    ProfileChain,
    load_profile_chain,
    pick_hive_catalog_for_item,
)
from mediatracker.core.profile_fallback import ProfileResolveError, resolve_profiles_for_media
from mediatracker.core.quality_profile import Profile, profile_from_row
from mediatracker.db.models import (
    Download,
    Link,
    Media,
    MediaItem,
    ProfileReplacementProposal,
    QualityProfile,
    TrashEntry,
)

_ACTIVE_PROPOSAL = "active"
_DISMISSED = "dismissed"
_STALE = "stale"
_REPLACING = "replacing"
_COMPLETED = "completed"


@dataclass(frozen=True)
class ProfileReplacementOut:
    id: int
    principal_profile_id: int
    principal_profile_name: str
    repli_profile_id: int
    repli_profile_name: str
    version_key: str


@dataclass
class EpisodeProfileIndex:
    """Batched profile replacement and served-profile fields for episode lists."""

    _proposals: dict[int, ProfileReplacementProposal]
    _poll_profile_id: dict[int, int]
    _rank_by_profile: dict[int, int]
    _profile_names: dict[int, str]

    @classmethod
    def load(cls, db: Session, media: Media, items: list[MediaItem], settings: Settings) -> EpisodeProfileIndex:
        item_ids = [item.id for item in items if item.id is not None]
        if not item_ids:
            return cls({}, {}, {}, {})

        proposals = {
            row.media_item_id: row
            for row in db.scalars(
                select(ProfileReplacementProposal).where(
                    ProfileReplacementProposal.media_item_id.in_(item_ids),
                    ProfileReplacementProposal.state == _ACTIVE_PROPOSAL,
                )
            ).all()
        }

        poll_by_item: dict[int, int] = {}
        for item in items:
            if item.id is None or item.served_poll_quality_profile_id is None:
                continue
            poll_by_item[item.id] = int(item.served_poll_quality_profile_id)
        # Comme served_poll_profile_id : un Download terminé peut porter le profil
        # même si downloaded est faux et que la colonne servie est vide.
        missing = [item_id for item_id in item_ids if item_id not in poll_by_item]
        if missing:
            for row in db.scalars(
                select(Download)
                .where(
                    Download.media_item_id.in_(missing),
                    Download.state == "done",
                    Download.poll_quality_profile_id.isnot(None),
                )
                .order_by(Download.id.desc())
            ).all():
                if row.media_item_id is not None and row.media_item_id not in poll_by_item:
                    poll_by_item[row.media_item_id] = int(row.poll_quality_profile_id)

        rank_by_profile: dict[int, int] = {}
        if poll_by_item:
            try:
                resolved = resolve_profiles_for_media(db, media, settings)
                rank_by_profile = {entry.profile_id: entry.rank for entry in resolved}
            except ProfileResolveError:
                rank_by_profile = {}

        profile_ids = set(rank_by_profile) | set(poll_by_item.values())
        for proposal in proposals.values():
            profile_ids.add(proposal.principal_profile_id)
            profile_ids.add(proposal.repli_profile_id)
        names: dict[int, str] = {}
        if profile_ids:
            for row in db.scalars(select(QualityProfile).where(QualityProfile.id.in_(profile_ids))).all():
                names[row.id] = row.name
        return cls(proposals, poll_by_item, rank_by_profile, names)

    def served_display_for(self, item: MediaItem) -> dict | None:
        if item.id is None:
            return None
        profile_id = self._poll_profile_id.get(item.id)
        if profile_id is None:
            return None
        rank = self._rank_by_profile.get(profile_id)
        if rank is None or rank <= 1:
            return None
        return {
            "profile_id": profile_id,
            "profile_name": self._profile_names.get(profile_id, f"#{profile_id}"),
            "repli": True,
        }

    def active_proposal(self, item_id: int) -> ProfileReplacementProposal | None:
        return self._proposals.get(item_id)

    def proposal_to_out(self, row: ProfileReplacementProposal) -> ProfileReplacementOut:
        return ProfileReplacementOut(
            id=row.id,
            principal_profile_id=row.principal_profile_id,
            principal_profile_name=self._profile_names.get(
                row.principal_profile_id, f"#{row.principal_profile_id}"
            ),
            repli_profile_id=row.repli_profile_id,
            repli_profile_name=self._profile_names.get(row.repli_profile_id, f"#{row.repli_profile_id}"),
            version_key=row.version_key,
        )


def _moment(now: datetime | None) -> datetime:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _profile_name(db: Session, profile_id: int) -> str:
    row = db.get(QualityProfile, profile_id)
    return row.name if row is not None else f"#{profile_id}"


def served_poll_profile_id(db: Session, item: MediaItem) -> int | None:
    if item.served_poll_quality_profile_id is not None:
        return int(item.served_poll_quality_profile_id)
    pid = db.scalar(
        select(Download.poll_quality_profile_id)
        .where(
            Download.media_item_id == item.id,
            Download.state == "done",
            Download.poll_quality_profile_id.isnot(None),
        )
        .order_by(Download.id.desc())
        .limit(1)
    )
    return int(pid) if pid is not None else None


def served_profile_rank(db: Session, media: Media, item: MediaItem, settings: Settings) -> int | None:
    """Rang dans la chaîne (1 = principal). None si inconnu."""
    pid = served_poll_profile_id(db, item)
    if pid is None:
        return None
    try:
        resolved = resolve_profiles_for_media(db, media, settings)
    except ProfileResolveError:
        return None
    for entry in resolved:
        if entry.profile_id == pid:
            return entry.rank
    return None


def record_served_profile_on_finish(db: Session, item: MediaItem, download: Download) -> None:
    pid = download.poll_quality_profile_id
    if pid is not None:
        item.served_poll_quality_profile_id = int(pid)


def replacing_proposal_for_download(db: Session, download: Download) -> ProfileReplacementProposal | None:
    return db.scalar(
        select(ProfileReplacementProposal).where(
            ProfileReplacementProposal.replace_download_id == download.id,
            ProfileReplacementProposal.state == _REPLACING,
        )
    )


def replacing_proposal_for_item(db: Session, item_id: int) -> ProfileReplacementProposal | None:
    return db.scalar(
        select(ProfileReplacementProposal).where(
            ProfileReplacementProposal.media_item_id == item_id,
            ProfileReplacementProposal.state == _REPLACING,
        )
    )


def revert_replacing_proposal(db: Session, proposal: ProfileReplacementProposal, *, now: datetime) -> None:
    proposal.state = _ACTIVE_PROPOSAL
    proposal.replace_download_id = None
    proposal.old_file_path = None
    proposal.updated_at = now
    db.flush()


def file_incoming_for_replacement(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    source: Path,
    dest: Path,
    occupant: Path | None,
    now: datetime,
) -> Path | None:
    """Garde le fichier entrant : corbeille de l'occupant via ``_trash_video``, restauration si échec."""
    from mediatracker.core.naming import atomic_move
    from mediatracker.core.pipeline.queue import _same_path, _trash_video

    trash_entry_id: int | None = None
    if occupant is not None and occupant.is_file() and not _same_path(source, occupant):
        trashed = _trash_video(db, settings, occupant, now, media, item)
        if trashed is None and occupant.exists():
            return None
        trash_entry_id = db.scalar(
            select(TrashEntry.id)
            .where(TrashEntry.media_item_id == item.id)
            .order_by(TrashEntry.id.desc())
            .limit(1)
        )
    if _same_path(source, dest) and dest.is_file():
        return dest
    from mediatracker.core.filing_integrity import check_source_before_move

    if check_source_before_move(db, source, media, item, now):
        if trash_entry_id is not None:
            _restore_trash_entry(db, settings, trash_entry_id, now=now)
        return None
    try:
        atomic_move(source, dest)
    except OSError:
        if trash_entry_id is not None:
            _restore_trash_entry(db, settings, trash_entry_id, now=now)
        return None
    return dest if dest.is_file() else None


def _restore_trash_entry(db: Session, settings: Settings, entry_id: int, *, now: datetime) -> None:
    from mediatracker.core.cleanup import CleanupError, restore

    try:
        restore(db, settings, entry_id, now=now)
    except CleanupError:
        pass


def _item_blocked_for_proposal(db: Session, settings: Settings, media: Media, item: MediaItem) -> bool:
    if media.keep or item.keep:
        return True
    status = FileStatusIndex.load(db, [item]).for_item(item)
    if status != "present":
        return True
    rank = served_profile_rank(db, media, item, settings)
    if rank is None or rank <= 1:
        return True
    if replacing_proposal_for_item(db, item.id) is not None:
        return True
    return False


def _version_key_for_link(link: Link) -> str:
    return f"link:{link.provider_id}:{link.external_id}"


def _version_key_for_hive(sha: str) -> str:
    return f"hive:{sha.lower()}"


def _principal_candidate(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    principal: Profile,
):
    from mediatracker.core.pipeline.jobs import _hosters
    from mediatracker.core.profile_attempt_adapters import choose_link_for_profile_attempt

    preference = _hosters(db, settings)
    links = list(db.scalars(select(Link).where(Link.media_item_id == item.id, Link.dead.is_(False))).all())
    from mediatracker.core.download_bitrate_sanity import filter_stored_links

    links = filter_stored_links(media, item, links)
    link = choose_link_for_profile_attempt(
        preference,
        links=links,
        profile=principal,
        skipped_link_ids=set(),
    )
    if link is not None:
        return _version_key_for_link(link), link, None
    chain = ProfileChain((principal,))
    pick = pick_hive_catalog_for_item(
        db,
        media,
        item,
        chain,
        prefer_available=True,
        single_profile=principal,
    )
    if pick is None or pick.match is None:
        return None
    match = pick.match
    if not match.row.disponible and not any(src.get("joignable") for src in match.sources):
        return None
    if match.expected_size <= 0:
        return None
    return _version_key_for_hive(match.sha256_hex), None, match.sha256_hex


def _dismissed_version_keys(db: Session, item_id: int) -> set[str]:
    rows = db.scalars(
        select(ProfileReplacementProposal.version_key).where(
            ProfileReplacementProposal.media_item_id == item_id,
            ProfileReplacementProposal.state == _DISMISSED,
        )
    ).all()
    return {str(value) for value in rows}


def _reactivate_proposal_row(
    row: ProfileReplacementProposal,
    *,
    principal_id: int,
    repli_id: int,
    version_key: str,
    link: Link | None,
    hive_sha: str | None,
    moment: datetime,
) -> None:
    row.state = _ACTIVE_PROPOSAL
    row.principal_profile_id = principal_id
    row.repli_profile_id = repli_id
    row.version_key = version_key
    row.link_id = link.id if link is not None else None
    row.hive_sha256_hex = hive_sha
    row.replace_download_id = None
    row.old_file_path = None
    row.updated_at = moment


def scan_proposals_after_poll(
    db: Session,
    media: Media,
    settings: Settings,
    *,
    now: datetime | None = None,
) -> int:
    """À la cadence des sondages : liens en cache + catalogue Hive, sans réseau."""
    moment = _moment(now)
    try:
        chain = load_profile_chain(db, media, settings)
    except ProfileResolveError:
        return 0
    principal = chain.principal
    if principal.quality_profile_id is None:
        return 0
    principal_id = int(principal.quality_profile_id)
    items = list(db.scalars(select(MediaItem).where(MediaItem.media_id == media.id)).all())
    created = 0
    last_repli_id: int | None = None
    for item in items:
        if _item_blocked_for_proposal(db, settings, media, item):
            _clear_stale_active(db, item.id, moment=moment)
            continue
        repli_id = served_poll_profile_id(db, item)
        if repli_id is None or repli_id == principal_id:
            _clear_stale_active(db, item.id, moment=moment)
            continue
        candidate = _principal_candidate(db, settings, media, item, principal)
        if candidate is None:
            _clear_stale_active(db, item.id, moment=moment)
            continue
        version_key, link, hive_sha = candidate
        if version_key in _dismissed_version_keys(db, item.id):
            continue
        existing = db.scalar(
            select(ProfileReplacementProposal).where(
                ProfileReplacementProposal.media_item_id == item.id,
                ProfileReplacementProposal.version_key == version_key,
            )
        )
        if existing is not None:
            if existing.state == _DISMISSED:
                continue
            if existing.state in {_ACTIVE_PROPOSAL, _REPLACING}:
                existing.updated_at = moment
                continue
            if existing.state in {_STALE, _COMPLETED}:
                _reactivate_proposal_row(
                    existing,
                    principal_id=principal_id,
                    repli_id=int(repli_id),
                    version_key=version_key,
                    link=link,
                    hive_sha=hive_sha,
                    moment=moment,
                )
                continue
        active = db.scalar(
            select(ProfileReplacementProposal).where(
                ProfileReplacementProposal.media_item_id == item.id,
                ProfileReplacementProposal.state == _ACTIVE_PROPOSAL,
            )
        )
        if active is not None and active.version_key != version_key:
            active.state = _STALE
            active.updated_at = moment
        if existing is None:
            db.add(
                ProfileReplacementProposal(
                    media_item_id=item.id,
                    principal_profile_id=principal_id,
                    repli_profile_id=int(repli_id),
                    version_key=version_key,
                    link_id=link.id if link is not None else None,
                    hive_sha256_hex=hive_sha,
                    state=_ACTIVE_PROPOSAL,
                    created_at=moment,
                    updated_at=moment,
                )
            )
            created += 1
            last_repli_id = int(repli_id)
    if created > 0:
        from mediatracker.metro.instrument import instrument_sui6_replacement_proposal
        from mediatracker.metro.record import safe_instrument

        safe_instrument(
            instrument_sui6_replacement_proposal,
            db=db,
            media=media,
            settings=settings,
            repli_profile_id=last_repli_id,
        )
    db.flush()
    return created


def _clear_stale_active(db: Session, item_id: int, *, moment: datetime | None = None) -> None:
    when = _moment(moment)
    for row in db.scalars(
        select(ProfileReplacementProposal).where(
            ProfileReplacementProposal.media_item_id == item_id,
            ProfileReplacementProposal.state == _ACTIVE_PROPOSAL,
        )
    ).all():
        row.state = _STALE
        row.updated_at = when


def active_proposal_for_item(db: Session, item_id: int) -> ProfileReplacementProposal | None:
    return db.scalar(
        select(ProfileReplacementProposal).where(
            ProfileReplacementProposal.media_item_id == item_id,
            ProfileReplacementProposal.state == _ACTIVE_PROPOSAL,
        )
    )


def proposal_to_out(db: Session, row: ProfileReplacementProposal) -> ProfileReplacementOut:
    return ProfileReplacementOut(
        id=row.id,
        principal_profile_id=row.principal_profile_id,
        principal_profile_name=_profile_name(db, row.principal_profile_id),
        repli_profile_id=row.repli_profile_id,
        repli_profile_name=_profile_name(db, row.repli_profile_id),
        version_key=row.version_key,
    )


def served_profile_display(db: Session, media: Media, item: MediaItem, settings: Settings) -> dict | None:
    """Ligne discrète « Profil utilisé » quand le fichier vient d'un repli."""
    rank = served_profile_rank(db, media, item, settings)
    if rank is None or rank <= 1:
        return None
    pid = served_poll_profile_id(db, item)
    if pid is None:
        return None
    return {"profile_id": pid, "profile_name": _profile_name(db, pid), "repli": True}


@dataclass(frozen=True)
class ReplaceResult:
    ok: bool
    message: str
    download_id: int | None = None


def dismiss_proposal(db: Session, item: MediaItem, *, now: datetime | None = None) -> bool:
    moment = _moment(now)
    row = active_proposal_for_item(db, item.id)
    if row is None:
        return False
    row.state = _DISMISSED
    row.updated_at = moment
    return True


def start_replace(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    *,
    now: datetime | None = None,
) -> ReplaceResult:
    moment = _moment(now)
    proposal = active_proposal_for_item(db, item.id)
    if proposal is None:
        return ReplaceResult(False, "Aucune proposition active.")
    if active_download_for_item(db, item.id) is not None:
        return ReplaceResult(False, "Un téléchargement est déjà en cours pour cet épisode.")
    old_path = item.file_path
    if not old_path:
        return ReplaceResult(False, "Fichier introuvable sur la fiche.")
    principal = db.get(QualityProfile, proposal.principal_profile_id)
    if principal is None:
        return ReplaceResult(False, "Profil principal introuvable.")
    profile = profile_from_row(principal)
    row = Download(
        media_item_id=item.id,
        state="pending",
        forced=True,
        poll_quality_profile_id=proposal.principal_profile_id,
        created_at=moment,
    )
    if proposal.link_id is not None:
        link = db.get(Link, proposal.link_id)
        if link is None or link.dead:
            return ReplaceResult(False, "Le lien proposé n'est plus disponible.")
        row.link_id = link.id
    db.add(row)
    db.flush()
    if proposal.hive_sha256_hex and proposal.link_id is None:
        from mediatracker.core.profile_attempt_adapters import pick_catalog_match_for_profile_attempt
        from mediatracker.hive.download_aria2 import attach_or_refresh_hive_fetch

        match = pick_catalog_match_for_profile_attempt(
            db,
            media,
            item,
            profile=profile,
            prefer_available=True,
            version_key=proposal.hive_sha256_hex,
            explicit_version=True,
            skip_profile_filter=True,
        )
        if match is None:
            db.delete(row)
            db.flush()
            return ReplaceResult(False, "Version Hive proposée introuvable dans le catalogue.")
        if attach_or_refresh_hive_fetch(db, settings, download=row, match=match, now=moment) is None:
            db.delete(row)
            db.flush()
            return ReplaceResult(False, "Impossible d'attacher le transfert Hive à ce téléchargement.")
    proposal.state = _REPLACING
    proposal.replace_download_id = row.id
    proposal.old_file_path = old_path
    proposal.updated_at = moment
    from mediatracker.core.worker_wake import REASON_API_MEDIA, wake_worker

    wake_worker(db, settings, REASON_API_MEDIA, commit=False)
    return ReplaceResult(True, "Remplacement lancé.", row.id)


def on_download_terminal(
    db: Session,
    settings: Settings,
    media: Media,
    item: MediaItem,
    download: Download,
    *,
    success: bool,
    new_dest: Path | None,
    now: datetime,
) -> None:
    proposal = replacing_proposal_for_download(db, download)
    if proposal is None:
        return
    if not success or new_dest is None or not new_dest.is_file():
        revert_replacing_proposal(db, proposal, now=now)
        return
    try:
        kept = Path(item.file_path or str(new_dest)).resolve()
        incoming = new_dest.resolve()
        if kept != incoming:
            revert_replacing_proposal(db, proposal, now=now)
            return
        if proposal.old_file_path:
            old = Path(proposal.old_file_path).resolve()
            if kept == old:
                revert_replacing_proposal(db, proposal, now=now)
                return
    except OSError:
        revert_replacing_proposal(db, proposal, now=now)
        return
    proposal.state = _COMPLETED
    item.served_poll_quality_profile_id = proposal.principal_profile_id
    proposal.updated_at = now
    db.flush()

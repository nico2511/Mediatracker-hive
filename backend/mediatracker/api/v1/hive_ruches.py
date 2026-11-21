"""Hive T3 : ruches, invitations et membres (API authentifiée)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from mediatracker.api.deps import get_db, require_scope
from mediatracker.api.errors import Problem
from mediatracker.config import Settings
from mediatracker.db.models import HiveRuche
from mediatracker.hive.catalog_epoch import ruche_confirm_name
from mediatracker.hive.identity import hive_enabled_in_db
from mediatracker.hive.invitations import create_invitation, revoke_invitation
from mediatracker.hive.member_list import current_members_as_records, latest_member_list_version
from mediatracker.hive.membership import (
    accept_join_pending,
    create_ruche,
    creator_control_code_for_pending,
    exclude_member,
    leave_ruche,
    local_instance_identity,
    open_pending_count_for_ruche,
    pending_for_ruche,
    preview_invitation_link,
    reject_join_pending,
    reset_ruche_catalog,
    ruches_for_instance,
)
from mediatracker.hive.peer_info import build_local_instance_snapshot, peer_info_row
from mediatracker.hive.peer_membership import confirm_membership_from_creator, submit_join_over_http
from mediatracker.hive.quota_journal import record_member_quota_reset
from mediatracker.hive.quota_member_notify import emit_my_quota_reset_notices
from mediatracker.hive.quota_peer import (
    aggregate_my_quota,
    queue_quota_reset_notice,
    resolve_member_display_name,
)
from mediatracker.hive.transfer_policy import (
    DEFAULT_WEEKLY_QUOTA,
    member_receive_quota_snapshot,
    reset_member_receive_quota,
)
from mediatracker.hive.transfer_settings import next_week_start_utc, week_start_date

router = APIRouter(tags=["hive"])


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_hive(db: Session) -> None:
    if not hive_enabled_in_db(db):
        raise Problem(404, "Introuvable", "Hive est désactivé.")


class RucheOut(BaseModel):
    ruche_id_hex: str
    creator_instance_id: str | None
    member_count: int
    is_creator: bool
    weekly_quota_bytes: int | None = None
    pending_join_count: int = 0


@router.get("/ruches", response_model=list[RucheOut])
def list_ruches(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> list[RucheOut]:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        _, instance_id, *_ = local_instance_identity(db, settings)
    except ValueError:
        return []
    rows = ruches_for_instance(db, instance_id)
    out: list[RucheOut] = []
    for r in rows:
        hive_ruche = db.get(HiveRuche, r.ruche_id)
        is_creator = r.creator_instance_id == instance_id
        out.append(
            RucheOut(
                ruche_id_hex=r.ruche_id.hex(),
                creator_instance_id=r.creator_instance_id,
                member_count=len(current_members_as_records(db, r.ruche_id)),
                is_creator=is_creator,
                weekly_quota_bytes=(
                    int(hive_ruche.weekly_quota_bytes)
                    if hive_ruche and hive_ruche.weekly_quota_bytes
                    else None
                ),
                pending_join_count=open_pending_count_for_ruche(db, r.ruche_id) if is_creator else 0,
            )
        )
    return out


@router.post("/ruches", response_model=RucheOut, status_code=201)
def post_create_ruche(
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> RucheOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        ruche = create_ruche(db, settings, now=_now())
        db.commit()
    except ValueError as exc:
        db.rollback()
        if str(exc) == "public_address_required":
            raise Problem(422, "Requête invalide", "Une adresse publique est requise pour créer une ruche.") from exc
        raise Problem(422, "Requête invalide", str(exc)) from exc
    _, instance_id, *_ = local_instance_identity(db, settings)
    return RucheOut(
        ruche_id_hex=ruche.ruche_id.hex(),
        creator_instance_id=ruche.creator_instance_id,
        member_count=len(current_members_as_records(db, ruche.ruche_id)),
        is_creator=ruche.creator_instance_id == instance_id,
        weekly_quota_bytes=int(ruche.weekly_quota_bytes) if ruche.weekly_quota_bytes else None,
    )


class InvitationOut(BaseModel):
    invitation_id: int
    link: str
    expires_at: str


class InvitationCreateIn(BaseModel):
    ruche_id_hex: str = Field(min_length=32, max_length=32)


@router.post("/ruches/invitations", response_model=InvitationOut, status_code=201)
def post_create_invitation(
    request: Request,
    body: InvitationCreateIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> InvitationOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    now = _now()
    try:
        pk, instance_id, _, _, pem, address = local_instance_identity(db, settings)
        ruche_id = bytes.fromhex(body.ruche_id_hex)
        row, link, _token = create_invitation(
            db,
            ruche_id=ruche_id,
            creator_id=instance_id,
            creator_address=address,
            creator_private_key=pk,
            creator_public_key_pem=pem,
            now=now,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code in {"creator_needs_public_address", "public_address_required"}:
            raise Problem(422, "Requête invalide", "Une adresse publique est requise pour inviter.") from exc
        if code == "not_creator":
            raise Problem(403, "Interdit", "Seul le créateur peut inviter.") from exc
        raise Problem(422, "Requête invalide", str(exc)) from exc
    return InvitationOut(
        invitation_id=row.id,
        link=link,
        expires_at=row.expires_at.isoformat(),
    )


@router.delete("/ruches/invitations/{invitation_id}", status_code=204)
def delete_invitation(
    request: Request,
    invitation_id: int,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    """Révoque une invitation Hive (admin / intégration, sans écran dédié dans l'UI)."""
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        _, instance_id, *_ = local_instance_identity(db, settings)
        revoke_invitation(db, invitation_id=invitation_id, creator_id=instance_id, now=_now())
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", str(exc)) from exc


class JoinPreviewIn(BaseModel):
    link: str


@router.post("/join/preview")
def post_join_preview(
    request: Request,
    body: JoinPreviewIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> dict:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        return preview_invitation_link(db, settings, link=body.link, now=_now())
    except ValueError as exc:
        raise Problem(422, "Requête invalide", str(exc)) from exc


class JoinBeginIn(BaseModel):
    link: str
    invitee_name: str = Field(min_length=2, max_length=32)
    invitee_public_address: str = ""


@router.post("/join/begin")
def post_join_begin(
    request: Request,
    body: JoinBeginIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> dict:
    """Soumet la demande d'adhésion au créateur via HTTP pair signé."""
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        out = submit_join_over_http(
            db,
            settings,
            link=body.link,
            invitee_name=body.invitee_name,
            invitee_public_address=body.invitee_public_address,
            now=_now(),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", str(exc)) from exc
    return out


class JoinConfirmIn(BaseModel):
    ruche_id_hex: str = Field(min_length=32, max_length=32)
    creator_base_url: str = Field(min_length=8)
    creator_instance_id: str = Field(min_length=4)


@router.post("/join/confirm")
def post_join_confirm(
    request: Request,
    body: JoinConfirmIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> dict:
    """Récupère la liste des membres signée après acceptation du créateur."""
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        version = confirm_membership_from_creator(
            db,
            settings,
            ruche_id=bytes.fromhex(body.ruche_id_hex),
            creator_base_url=body.creator_base_url.strip(),
            creator_instance_id=body.creator_instance_id.strip(),
            now=_now(),
        )
        db.commit()
    except PermissionError as exc:
        db.rollback()
        raise Problem(401, "Non autorisé", "Accès refusé par le pair.") from exc
    except ValueError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", str(exc)) from exc
    return {"member_list_version": version}


class JoinAcceptIn(BaseModel):
    pending_id: int


@router.post("/join/accept", status_code=204)
def post_join_accept(
    request: Request,
    body: JoinAcceptIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        accept_join_pending(db, settings, pending_id=body.pending_id, now=_now())
        db.commit()
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "not_creator":
            raise Problem(403, "Interdit", "Seul le créateur peut accepter.") from exc
        raise Problem(422, "Requête invalide", str(exc)) from exc


class JoinRejectIn(BaseModel):
    pending_id: int


@router.post("/join/reject", status_code=204)
def post_join_reject(
    request: Request,
    body: JoinRejectIn,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        reject_join_pending(db, settings, pending_id=body.pending_id, now=_now())
        db.commit()
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "not_creator":
            raise Problem(403, "Interdit", "Seul le créateur peut refuser.") from exc
        raise Problem(422, "Requête invalide", str(exc)) from exc


@router.get("/join/pending/{ruche_id_hex}")
def get_join_pending(
    ruche_id_hex: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> list[dict]:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    ruche_id = bytes.fromhex(ruche_id_hex)
    try:
        _, instance_id, *_ = local_instance_identity(db, settings)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Identité Hive absente.") from exc
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        raise Problem(404, "Introuvable", "Ruche introuvable.")
    if ruche.creator_instance_id != instance_id:
        raise Problem(403, "Interdit", "Seul le créateur peut voir les demandes.")
    items = pending_for_ruche(db, ruche_id)
    out = []
    for p in items:
        try:
            code = creator_control_code_for_pending(db, settings, pending_id=p.id)
        except ValueError:
            code = None
        invitee_name = None
        try:
            doc = json.loads(p.invitee_name_document_json)
            if isinstance(doc, dict) and isinstance(doc.get("nom"), str):
                invitee_name = doc["nom"]
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
        out.append(
            {
                "pending_id": p.id,
                "invitee_instance_id": p.invitee_instance_id,
                "invitee_name": invitee_name,
                "control_code": code,
            }
        )
    return out


@router.get("/ruches/{ruche_id_hex}/members")
def get_ruche_members(
    ruche_id_hex: str,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> dict:
    del _principal
    _require_hive(db)
    ruche_id = bytes.fromhex(ruche_id_hex)
    members = current_members_as_records(db, ruche_id)
    version = latest_member_list_version(db, ruche_id)
    return {"version": version, "members": members}


class ExcludeIn(BaseModel):
    instance_id: str


@router.post("/ruches/{ruche_id_hex}/exclude", status_code=204)
def post_exclude_member(
    ruche_id_hex: str,
    body: ExcludeIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        exclude_member(
            db, settings, ruche_id=bytes.fromhex(ruche_id_hex), target_instance_id=body.instance_id, now=_now()
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", str(exc)) from exc


@router.post("/ruches/{ruche_id_hex}/leave", status_code=204)
def post_leave_ruche(
    ruche_id_hex: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        leave_ruche(db, settings, ruche_id=bytes.fromhex(ruche_id_hex), now=_now())
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise Problem(422, "Requête invalide", str(exc)) from exc


class MemberUiOut(BaseModel):
    instance_id: str
    display_name: str
    pull_only: bool
    is_self: bool
    app_version: str | None = None
    hive_protocol: int = 0
    capabilities: list[str] = Field(default_factory=list)
    shares_in_ruche: bool = False
    receive_bytes_used: int | None = None
    receive_quota_bytes: int | None = None
    receive_quota_status: str | None = None


class RucheDetailOut(BaseModel):
    ruche_id_hex: str
    is_creator: bool
    weekly_quota_bytes: int
    quota_week_start: str
    quota_reset_at: str
    catalog_epoch: int = 0
    catalog_reset_at: str | None = None
    members: list[MemberUiOut]


class MyQuotaSourceOut(BaseModel):
    status: str
    source_instance_id: str
    source_display_name: str
    shares_in_ruche: bool | None = None
    bytes_used: int | None = None
    quota_bytes: int | None = None
    week_start: str | None = None
    reset_at: str | None = None
    receive_quota_status: str | None = None


class MyQuotaOut(BaseModel):
    ruche_id_hex: str
    week_start: str
    reset_at: str
    sources: list[MyQuotaSourceOut]


@router.get("/ruches/{ruche_id_hex}/my-quota", response_model=MyQuotaOut)
def get_my_quota(
    ruche_id_hex: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> MyQuotaOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        ruche_id = bytes.fromhex(ruche_id_hex)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Ruche introuvable.") from exc
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        raise Problem(404, "Introuvable", "Ruche introuvable.")
    now = _now()
    try:
        raw = aggregate_my_quota(db, settings, ruche_id=ruche_id, now=now)
        emit_my_quota_reset_notices(db, sources=raw.get("sources") or [], when=now)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise Problem(404, "Introuvable", "Identité Hive absente.") from exc
    sources = [
        MyQuotaSourceOut.model_validate(row) for row in raw.get("sources") or [] if isinstance(row, dict)
    ]
    return MyQuotaOut(
        ruche_id_hex=str(raw.get("ruche_id_hex") or ruche_id_hex),
        week_start=str(raw.get("week_start") or ""),
        reset_at=str(raw.get("reset_at") or ""),
        sources=sources,
    )


@router.get("/ruches/{ruche_id_hex}/detail", response_model=RucheDetailOut)
def get_ruche_detail(
    ruche_id_hex: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("read")),
) -> RucheDetailOut:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    ruche_id = bytes.fromhex(ruche_id_hex)
    try:
        _, instance_id, *_ = local_instance_identity(db, settings)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Identité Hive absente.") from exc
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        raise Problem(404, "Introuvable", "Ruche introuvable.")
    quota = int(ruche.weekly_quota_bytes) if ruche.weekly_quota_bytes else DEFAULT_WEEKLY_QUOTA
    now = _now()
    local_instance = build_local_instance_snapshot(now)
    members_ui: list[MemberUiOut] = []
    for m in current_members_as_records(db, ruche_id):
        doc = m.get("nom_doc") if isinstance(m.get("nom_doc"), dict) else {}
        if not isinstance(doc, dict):
            doc = {}
        name = doc.get("nom") if isinstance(doc.get("nom"), str) else None
        mid = str(m["id"])
        is_self = mid == instance_id
        if is_self:
            app_version = str(local_instance["version"])
            hive_protocol = int(local_instance["hive_protocol"])
            capabilities = list(local_instance.get("capabilities") or [])
        else:
            info = peer_info_row(db, ruche_id=ruche_id, origin_instance_id=mid)
            if info is None:
                app_version = None
                hive_protocol = 0
                capabilities = []
            else:
                app_version = info.app_version
                hive_protocol = info.hive_protocol
                capabilities = list(info.capabilities)
        quota_snap = member_receive_quota_snapshot(db, ruche_id=ruche_id, instance_id=mid, now=now)
        members_ui.append(
            MemberUiOut(
                instance_id=mid,
                display_name=(name or str(m["id"])[:8]),
                pull_only=str(m.get("address") or "") == "tirage",
                is_self=is_self,
                app_version=app_version,
                hive_protocol=hive_protocol,
                capabilities=capabilities,
                shares_in_ruche=bool(quota_snap["shares_in_ruche"]),
                receive_bytes_used=quota_snap["receive_bytes_used"],
                receive_quota_bytes=quota_snap["receive_quota_bytes"],
                receive_quota_status=quota_snap["receive_quota_status"],
            )
        )
    reset_at = ruche.catalog_reset_at.isoformat() if ruche.catalog_reset_at is not None else None
    ws = week_start_date(now)
    nxt = next_week_start_utc(now)
    return RucheDetailOut(
        ruche_id_hex=ruche_id_hex,
        is_creator=ruche.creator_instance_id == instance_id,
        weekly_quota_bytes=quota,
        quota_week_start=ws.isoformat(),
        quota_reset_at=nxt.isoformat().replace("+00:00", "Z"),
        catalog_epoch=int(ruche.catalog_epoch or 0),
        catalog_reset_at=reset_at,
        members=members_ui,
    )


class CatalogResetIn(BaseModel):
    confirm_name: str = Field(min_length=1, max_length=80)


@router.post("/ruches/{ruche_id_hex}/catalog-reset", status_code=204)
def post_catalog_reset(
    ruche_id_hex: str,
    body: CatalogResetIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    """Réinitialise le catalogue de la ruche sur toutes les instances (créateur, nom confirmé)."""
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        ruche_id = bytes.fromhex(ruche_id_hex)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Ruche introuvable.") from exc
    try:
        reset_ruche_catalog(
            db,
            settings,
            ruche_id=ruche_id,
            confirm_name=body.confirm_name,
            now=_now(),
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "not_creator":
            raise Problem(403, "Interdit", "Seul le créateur peut réinitialiser le catalogue.") from exc
        if code == "confirm_name_mismatch":
            expected = ruche_confirm_name(ruche_id)
            raise Problem(
                400,
                "Requête invalide",
                f"Le nom ne correspond pas. Saisissez exactement : {expected}",
            ) from exc
        raise Problem(422, "Requête invalide", str(exc)) from exc


class QuotaPatchIn(BaseModel):
    weekly_quota_bytes: int = Field(ge=1, le=10_000_000_000_000)


@router.post("/ruches/{ruche_id_hex}/members/{instance_id}/quota/reset", status_code=204)
def post_member_quota_reset(
    ruche_id_hex: str,
    instance_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    try:
        ruche_id = bytes.fromhex(ruche_id_hex)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Ruche introuvable.") from exc
    try:
        _, local_id, *_ = local_instance_identity(db, settings)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Identité Hive absente.") from exc
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None:
        raise Problem(404, "Introuvable", "Ruche introuvable.")
    if ruche.creator_instance_id != local_id:
        raise Problem(403, "Interdit", "Seul le créateur peut remettre un quota à zéro.")
    now = _now()
    member_label = resolve_member_display_name(db, ruche_id=ruche_id, instance_id=instance_id)
    try:
        reset_member_receive_quota(db, ruche_id=ruche_id, member_id=instance_id, now=now)
        record_member_quota_reset(
            db,
            ruche_id_hex=ruche_id_hex,
            member_instance_id=instance_id,
            member_display=member_label,
            when=now,
        )
        queue_quota_reset_notice(db, ruche_id=ruche_id, member_instance_id=instance_id, when=now)
        db.commit()
    except ValueError as exc:
        db.rollback()
        code = str(exc)
        if code == "member_not_found":
            raise Problem(404, "Introuvable", "Membre introuvable.") from exc
        if code == "shares_in_ruche":
            raise Problem(
                422,
                "Requête invalide",
                "Ce membre partage dans la ruche : aucun quota à remettre à zéro.",
            ) from exc
        raise Problem(422, "Requête invalide", str(exc)) from exc


@router.patch("/ruches/{ruche_id_hex}/quota", status_code=204)
def patch_ruche_quota(
    ruche_id_hex: str,
    body: QuotaPatchIn,
    request: Request,
    db: Session = Depends(get_db),
    _principal=Depends(require_scope("admin")),
) -> None:
    del _principal
    _require_hive(db)
    settings = _settings(request)
    ruche_id = bytes.fromhex(ruche_id_hex)
    try:
        _, instance_id, *_ = local_instance_identity(db, settings)
    except ValueError as exc:
        raise Problem(404, "Introuvable", "Identité Hive absente.") from exc
    ruche = db.get(HiveRuche, ruche_id)
    if ruche is None or ruche.creator_instance_id != instance_id:
        raise Problem(403, "Interdit", "Seul le créateur peut régler le quota.")
    ruche.weekly_quota_bytes = int(body.weekly_quota_bytes)
    db.commit()

"""Routes pair Hive sous /hive/ (signatures, sans session)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from mediatracker.hive.chunk_digest_store import ChunkDigestsNotReady
from mediatracker.hive.fetch_grant import (
    HIVE_FETCH_GRANT_HEADER,
    FetchGrantDenied,
    mint_fetch_grant,
    revoke_fetch_grant_by_id,
    verify_fetch_grant,
)
from mediatracker.hive.file_serve import (
    finalize_response_headers,
    iter_file_byte_range,
    persist_inbound_quota_bytes,
    prepare_file_response,
    resolve_local_file,
)
from mediatracker.hive.hive_busy import (
    DEFAULT_RETRY_BANDWIDTH,
    DEFAULT_RETRY_PARALLEL,
    DEFAULT_RETRY_RATE,
    HIVE_BUSY_REASON_HEADER,
    REASON_BANDWIDTH,
    REASON_PARALLEL,
    REASON_QUOTA,
    REASON_RATE,
    REASON_WINDOW,
)
from mediatracker.hive.member_list import latest_signed_member_list
from mediatracker.hive.peer_auth import PeerAuthDenied, PeerAuthMalformed
from mediatracker.hive.peer_gossip import (
    GOSSIP_PAGE_DEFAULT,
    entries_page_for_peer,
    ingest_signed_entries,
    record_reachability,
    signals_page_for_peer,
    version_vector_for_ruche,
)
from mediatracker.hive.peer_gossip_vectors import record_peer_gossip_vectors
from mediatracker.hive.peer_inbound import authenticate_join_peer, authenticate_member_peer
from mediatracker.hive.peer_info import build_local_instance_snapshot, ingest_peer_info
from mediatracker.hive.peer_join import process_peer_join_preview, process_peer_join_request
from mediatracker.hive.peer_rate_limit import PeerRateLimited
from mediatracker.hive.quota_peer import quota_for_requester_payload
from mediatracker.hive.signal import ingest_signed_signals, signal_version_vector_for_ruche
from mediatracker.hive.signal_requests import try_auto_accept_incoming
from mediatracker.hive.transfer_bandwidth import TransferBandwidthDenied, TransferBandwidthLimiter
from mediatracker.hive.transfer_load import build_local_load_snapshot, ingest_peer_load
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.hive.transfer_policy import TransferQuotaExceeded, TransferWindowClosed
from mediatracker.hive.transfer_settings import (
    in_transfer_window,
    seconds_until_transfer_window_open,
    seconds_until_week_reset,
    transfer_parallel_limit,
)

_MAX_PEER_BODY = 1_000_000

logger = logging.getLogger(__name__)


def _warn_hive_peer_denied(reason: str, *, kind: str) -> None:
    code = (reason or "unknown").strip()[:32]
    if code:
        logger.warning("hive %s denied: %s", kind, code)


def _maybe_revoke_completed_grant(app, grant_id: int, *, content_size: int, bytes_sent: int) -> None:
    if grant_id <= 0 or content_size <= 0 or bytes_sent < content_size:
        return
    session = app.state.session_factory()
    try:
        if revoke_fetch_grant_by_id(session, grant_id=grant_id, now=_now()):
            session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _peer_404():
    return PlainTextResponse("Not Found", status_code=404)


def _peer_401():
    return PlainTextResponse("Unauthorized", status_code=401)


def _peer_429(reason: str | None = None, retry_after: int | None = None):
    headers: dict[str, str] = {}
    if reason:
        headers[HIVE_BUSY_REASON_HEADER] = reason
    if retry_after is not None:
        headers["Retry-After"] = str(max(1, int(retry_after)))
    return PlainTextResponse("Too Many Requests", status_code=429, headers=headers)


def _peer_503():
    from mediatracker.hive.chunk_digest_store import HIVE_CHUNKS_PENDING_HEADER

    return PlainTextResponse(
        "Chunk digests not ready",
        status_code=503,
        headers={HIVE_CHUNKS_PENDING_HEADER: "1"},
    )


def _enforce_peer_rate(request: Request, hive_id: str) -> None:
    limiter = getattr(request.app.state, "hive_peer_limiter", None)
    if limiter is not None:
        limiter.check(hive_id)


def _parse_ruche_id(ruche_id_hex: str) -> bytes | None:
    try:
        ruche_id = bytes.fromhex(ruche_id_hex)
    except ValueError:
        return None
    if len(ruche_id) != 16:
        return None
    return ruche_id


def mount_hive_router(app) -> None:
    router = APIRouter(prefix="/hive")

    @router.post("/ruche/{ruche_id_hex}/join/preview")
    async def hive_join_preview(ruche_id_hex: str, request: Request):
        try:
            ruche_id = bytes.fromhex(ruche_id_hex)
        except ValueError:
            return _peer_404()
        if len(ruche_id) != 16:
            return _peer_404()
        body = await request.body()
        try:
            join_doc = json.loads(body.decode("utf-8"))
            pem = join_doc.get("invitee_public_key_pem")
            if not isinstance(pem, str):
                return _peer_404()
            from mediatracker.hive.crypto_id import hive_public_id_from_public_key, public_key_from_pem

            invitee_id = hive_public_id_from_public_key(public_key_from_pem(pem.encode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
            return _peer_404()

        session = request.app.state.session_factory()
        now = _now()
        try:
            authenticate_join_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=body,
                ruche_id=ruche_id,
                invitee_public_key_pem=pem,
                invitee_instance_id=invitee_id,
                now=now,
            )
            out = process_peer_join_preview(session, ruche_id=ruche_id, body=body, now=now)
            session.commit()
            return JSONResponse(out)
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except ValueError as exc:
            session.rollback()
            return JSONResponse({"error": str(exc)}, status_code=422)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @router.post("/ruche/{ruche_id_hex}/join/request")
    async def hive_join_request(ruche_id_hex: str, request: Request):
        try:
            ruche_id = bytes.fromhex(ruche_id_hex)
        except ValueError:
            return _peer_404()
        if len(ruche_id) != 16:
            return _peer_404()
        body = await request.body()
        try:
            join_doc = json.loads(body.decode("utf-8"))
            pem = join_doc.get("invitee_public_key_pem")
            name_doc = join_doc.get("invitee_name_document")
            name_sig = join_doc.get("invitee_name_signature_b64")
            if not isinstance(pem, str) or not isinstance(name_doc, dict) or not isinstance(name_sig, str):
                return _peer_404()
            from mediatracker.hive.crypto_id import hive_public_id_from_public_key, public_key_from_pem

            invitee_id = hive_public_id_from_public_key(public_key_from_pem(pem.encode("utf-8")))
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
            return _peer_404()

        session = request.app.state.session_factory()
        now = _now()
        try:
            authenticate_join_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=body,
                ruche_id=ruche_id,
                invitee_public_key_pem=pem,
                invitee_instance_id=invitee_id,
                now=now,
            )
            out = process_peer_join_request(
                session, request.app.state.settings, ruche_id=ruche_id, body=body, now=now
            )
            session.commit()
            return JSONResponse(out)
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except ValueError:
            session.rollback()
            return _peer_401()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @router.get("/ruche/{ruche_id_hex}/member-list")
    async def hive_member_list(ruche_id_hex: str, request: Request):
        try:
            ruche_id = bytes.fromhex(ruche_id_hex)
        except ValueError:
            return _peer_404()
        if len(ruche_id) != 16:
            return _peer_404()
        body = b""
        session = request.app.state.session_factory()
        now = _now()
        try:
            ctx = authenticate_member_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=body,
                ruche_id=ruche_id,
                now=now,
            )
            _enforce_peer_rate(request, ctx.hive_id)
            record_reachability(session, ruche_id=ruche_id, origin_id=ctx.hive_id, now=now)
            doc = latest_signed_member_list(session, ruche_id)
            if doc is None:
                return _peer_401()
            payload, signature_b64 = doc
            session.commit()
            return JSONResponse({"payload": payload, "signature_b64": signature_b64})
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
        finally:
            session.close()

    @router.get("/ruche/{ruche_id_hex}/quota-for-peer")
    async def hive_quota_for_peer(ruche_id_hex: str, request: Request):
        ruche_id = _parse_ruche_id(ruche_id_hex)
        if ruche_id is None:
            return _peer_404()
        body = b""
        session = request.app.state.session_factory()
        now = _now()
        settings = request.app.state.settings
        try:
            ctx = authenticate_member_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=body,
                ruche_id=ruche_id,
                now=now,
            )
            _enforce_peer_rate(request, ctx.hive_id)
            record_reachability(session, ruche_id=ruche_id, origin_id=ctx.hive_id, now=now)
            try:
                payload = quota_for_requester_payload(
                    session,
                    settings,
                    ruche_id=ruche_id,
                    requester_id=ctx.hive_id,
                    now=now,
                )
            except ValueError:
                session.rollback()
                return _peer_401()
            session.commit()
            return JSONResponse(payload)
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
        finally:
            session.close()

    @router.get("/ruche/{ruche_id_hex}/access-check")
    async def hive_access_check(ruche_id_hex: str, request: Request):
        try:
            ruche_id = bytes.fromhex(ruche_id_hex)
        except ValueError:
            return _peer_404()
        if len(ruche_id) != 16:
            return _peer_404()
        session = request.app.state.session_factory()
        now = _now()
        try:
            ctx = authenticate_member_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=b"",
                ruche_id=ruche_id,
                now=now,
            )
            _enforce_peer_rate(request, ctx.hive_id)
            record_reachability(session, ruche_id=ruche_id, origin_id=ctx.hive_id, now=now)
            session.commit()
            return Response(status_code=204)
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
        finally:
            session.close()

    @router.post("/ruche/{ruche_id_hex}/gossip/sync")
    async def hive_gossip_sync(ruche_id_hex: str, request: Request):
        ruche_id = _parse_ruche_id(ruche_id_hex)
        if ruche_id is None:
            return _peer_404()
        body = await request.body()
        if len(body) > _MAX_PEER_BODY:
            return _peer_404()
        try:
            doc = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _peer_404()
        peer_vector = doc.get("vector")
        inbound = doc.get("entries")
        if not isinstance(peer_vector, dict):
            return _peer_404()
        vector: dict[str, int] = {}
        for k, v in peer_vector.items():
            if isinstance(k, str) and isinstance(v, int):
                vector[k] = v
        entries_in: list = inbound if isinstance(inbound, list) else []
        peer_signal_vector = doc.get("signal_vector")
        signal_vector_in: dict[str, int] = {}
        if isinstance(peer_signal_vector, dict):
            for k, v in peer_signal_vector.items():
                if isinstance(k, str) and isinstance(v, int):
                    signal_vector_in[k] = v
        signals_in: list = doc.get("signals") if isinstance(doc.get("signals"), list) else []
        cursor = doc.get("cursor")
        signal_cursor = doc.get("signal_cursor")
        limit_raw = doc.get("limit")
        limit = GOSSIP_PAGE_DEFAULT
        if isinstance(limit_raw, int) and 1 <= limit_raw <= GOSSIP_PAGE_DEFAULT:
            limit = limit_raw
        page_cursor = cursor if isinstance(cursor, dict) else None
        signal_page_cursor = signal_cursor if isinstance(signal_cursor, dict) else None
        is_hint = doc.get("hint") is True

        session = request.app.state.session_factory()
        now = _now()
        settings = request.app.state.settings
        try:
            ctx = authenticate_member_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=body,
                ruche_id=ruche_id,
                now=now,
            )
            _enforce_peer_rate(request, ctx.hive_id)
            record_reachability(session, ruche_id=ruche_id, origin_id=ctx.hive_id, now=now)
            record_peer_gossip_vectors(
                session,
                ruche_id=ruche_id,
                peer_instance_id=ctx.hive_id,
                vector=vector,
                signal_vector=signal_vector_in,
                now=now,
            )
            if doc.get("load") is not None:
                ingest_peer_load(
                    session,
                    ruche_id=ruche_id,
                    origin_instance_id=ctx.hive_id,
                    load=doc.get("load"),
                    now=now,
                )
            if doc.get("instance") is not None:
                ingest_peer_info(
                    session,
                    ruche_id=ruche_id,
                    origin_instance_id=ctx.hive_id,
                    instance=doc.get("instance"),
                    now=now,
                )
            entries_accepted = ingest_signed_entries(
                session,
                ruche_id=ruche_id,
                items=entries_in,
                now=now,
                settings=settings,
                allow_seq_gap=not is_hint,
            )
            signals_accepted = ingest_signed_signals(
                session, ruche_id=ruche_id, items=signals_in, now=now, allow_seq_gap=not is_hint
            )
            if entries_accepted > 0 or signals_accepted > 0:
                from mediatracker.hive.catalog_sync_gate import mark_hive_catalog_sync_needed

                mark_hive_catalog_sync_needed(session)
            try_auto_accept_incoming(session, settings, ruche_id=ruche_id, now=now)
            our_vector = version_vector_for_ruche(session, ruche_id)
            our_signal_vector = signal_version_vector_for_ruche(session, ruche_id)
            parallel_gate = getattr(request.app.state, "hive_transfer_parallel", None)
            send_bandwidth = getattr(request.app.state, "hive_send_bandwidth", None)
            our_load = build_local_load_snapshot(
                session,
                now,
                parallel_gate=parallel_gate if isinstance(parallel_gate, TransferParallelGate) else None,
                send_bandwidth=send_bandwidth if isinstance(send_bandwidth, TransferBandwidthLimiter) else None,
            )
            our_instance = build_local_instance_snapshot(now)
            if is_hint:
                session.commit()
                hint_body: dict[str, object] = {
                    "vector": our_vector,
                    "entries": [],
                    "has_more": False,
                    "cursor": None,
                    "signal_vector": our_signal_vector,
                    "signals": [],
                    "signal_has_more": False,
                    "signal_cursor": None,
                    "instance": our_instance,
                }
                if our_load is not None:
                    hint_body["load"] = our_load
                return JSONResponse(hint_body)
            page = entries_page_for_peer(
                session, ruche_id=ruche_id, peer_vector=vector, limit=limit, cursor=page_cursor
            )
            sig_page = signals_page_for_peer(
                session,
                ruche_id=ruche_id,
                peer_vector=signal_vector_in,
                limit=limit,
                cursor=signal_page_cursor,
            )
            session.commit()
            sync_body: dict[str, object] = {
                "vector": our_vector,
                "entries": page.entries,
                "has_more": page.has_more,
                "cursor": page.cursor,
                "signal_vector": our_signal_vector,
                "signals": sig_page.entries,
                "signal_has_more": sig_page.has_more,
                "signal_cursor": sig_page.cursor,
                "instance": our_instance,
            }
            if our_load is not None:
                sync_body["load"] = our_load
            return JSONResponse(sync_body)
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
        finally:
            session.close()

    def _parse_file_id(file_id_hex: str) -> bytes | None:
        if ".." in file_id_hex or len(file_id_hex) != 32:
            return None
        try:
            return bytes.fromhex(file_id_hex)
        except ValueError:
            return None

    def _ruche_from_headers(request: Request) -> bytes | None:
        ruche_hex = request.headers.get("Hive-Ruche") or request.headers.get("hive-ruche")
        if not ruche_hex:
            return None
        return _parse_ruche_id(ruche_hex.strip())

    def _fetch_grant_header(request: Request) -> str | None:
        raw = request.headers.get(HIVE_FETCH_GRANT_HEADER) or request.headers.get(
            HIVE_FETCH_GRANT_HEADER.lower()
        )
        if not raw or not str(raw).strip():
            return None
        return str(raw).strip()

    @router.post("/ruche/{ruche_id_hex}/files/{file_id_hex}/fetch-grant")
    async def hive_fetch_grant_mint(ruche_id_hex: str, file_id_hex: str, request: Request):
        ruche_id = _parse_ruche_id(ruche_id_hex)
        if ruche_id is None:
            return _peer_404()
        file_id = _parse_file_id(file_id_hex)
        if file_id is None:
            return _peer_404()
        body = b""
        session = request.app.state.session_factory()
        now = _now()
        try:
            ctx = authenticate_member_peer(
                session,
                headers=request.headers,
                method=request.method,
                path=request.url.path,
                query=request.url.query or "",
                body=body,
                ruche_id=ruche_id,
                now=now,
            )
            _enforce_peer_rate(request, ctx.hive_id)
            if not in_transfer_window(session, now):
                session.rollback()
                return _peer_429(
                    REASON_WINDOW,
                    seconds_until_transfer_window_open(session, now),
                )
            try:
                minted = mint_fetch_grant(
                    session,
                    request.app.state.settings,
                    ruche_id=ruche_id,
                    file_id=file_id,
                    requester_id=ctx.hive_id,
                    now=now,
                )
            except FetchGrantDenied as exc:
                session.rollback()
                _warn_hive_peer_denied(exc.reason, kind="fetch-grant mint")
                if exc.reason == "file":
                    return _peer_404()
                return _peer_401()
            session.commit()
            from mediatracker.hive.file_serve import resolve_local_file

            binding = resolve_local_file(
                session, request.app.state.settings, ruche_id=ruche_id, file_id=file_id
            )
            payload = {
                "grant": minted.grant_secret,
                "expires_at": minted.expires_at.isoformat(),
            }
            if binding is not None:
                payload["content_size"] = int(binding.size)
                payload["content_mtime_ns"] = int(binding.mtime_ns)
            return JSONResponse(payload)
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
        finally:
            session.close()

    @router.get("/files/{file_id_hex}", operation_id="hive_serve_file_get")
    @router.head("/files/{file_id_hex}", operation_id="hive_serve_file_head")
    async def hive_file_download(file_id_hex: str, request: Request):
        file_id = _parse_file_id(file_id_hex)
        if file_id is None:
            return _peer_404()
        ruche_id = _ruche_from_headers(request)
        if ruche_id is None:
            return _peer_404()
        body = b""
        session = request.app.state.session_factory()
        now = _now()
        plan = None
        grant_secret = _fetch_grant_header(request)
        is_head = request.method.upper() == "HEAD"
        grant_id: int | None = None
        parallel_acquired = False
        parallel_gate: TransferParallelGate | None = None
        record_quota_bytes = False
        try:
            # En-tête grant présent : fail-closed (401 si invalide), jamais de repli auth pair.
            if grant_secret is not None:
                try:
                    grant_ctx = verify_fetch_grant(
                        session,
                        request.app.state.settings,
                        grant_secret=grant_secret,
                        ruche_id=ruche_id,
                        file_id=file_id,
                        now=now,
                    )
                except FetchGrantDenied as exc:
                    session.rollback()
                    _warn_hive_peer_denied(exc.reason, kind="fetch-grant verify")
                    return _peer_401()
                requester_id = grant_ctx.requester_instance_id
                grant_id = int(grant_ctx.grant_id)
            else:
                ctx = authenticate_member_peer(
                    session,
                    headers=request.headers,
                    method=request.method,
                    path=request.url.path,
                    query=request.url.query or "",
                    body=body,
                    ruche_id=ruche_id,
                    now=now,
                )
                requester_id = ctx.hive_id
            # Depuis LOT4, seul aria2 + fetch-grant télécharge ; pas de morceaux sur la réponse.
            require_chunks = False
            if grant_secret is None:
                _enforce_peer_rate(request, requester_id)
            binding = resolve_local_file(session, request.app.state.settings, ruche_id=ruche_id, file_id=file_id)
            if binding is None:
                session.rollback()
                return _peer_404()
            range_header = request.headers.get("Range") or request.headers.get("range")
            record_quota_bytes = not is_head
            try:
                plan = prepare_file_response(
                    session,
                    binding=binding,
                    requester_id=requester_id,
                    range_header=range_header,
                    now=now,
                    require_chunk_digests=require_chunks,
                    record_transfer_bytes=record_quota_bytes,
                )
            except TransferQuotaExceeded:
                session.rollback()
                return _peer_429(REASON_QUOTA, seconds_until_week_reset(now))
            except TransferWindowClosed:
                session.rollback()
                return _peer_429(
                    REASON_WINDOW,
                    seconds_until_transfer_window_open(session, now),
                )
            except ChunkDigestsNotReady:
                session.rollback()
                from mediatracker.hive.fingerprint_wake import poke_fingerprint_fifo_only

                await asyncio.to_thread(poke_fingerprint_fifo_only, request.app.state.settings)
                return _peer_503()
            if plan is None:
                session.rollback()
                return PlainTextResponse("Requested Range Not Satisfiable", status_code=416)
            gate = getattr(request.app.state, "hive_transfer_parallel", None)
            if grant_id is not None and not is_head and isinstance(gate, TransferParallelGate):
                parallel_gate = gate
                slot_limit = transfer_parallel_limit(session)
                if not parallel_gate.acquire(grant_id, slot_limit):
                    session.rollback()
                    return _peer_429(REASON_PARALLEL, DEFAULT_RETRY_PARALLEL)
                parallel_acquired = True
            limiter = getattr(request.app.state, "hive_send_bandwidth", None)
            if limiter is not None and not is_head:
                try:
                    limiter.reserve(requester_id, plan.byte_range.length)
                except TransferBandwidthDenied:
                    session.rollback()
                    if parallel_acquired and parallel_gate is not None and grant_id is not None:
                        parallel_gate.release(grant_id)
                    return _peer_429(REASON_BANDWIDTH, DEFAULT_RETRY_BANDWIDTH)
            try:
                session.commit()
            except Exception:
                session.rollback()
                if parallel_acquired and parallel_gate is not None and grant_id is not None:
                    parallel_gate.release(grant_id)
                raise
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied as exc:
            session.rollback()
            reason = str(exc.args[0]) if exc.args else "member"
            _warn_hive_peer_denied(reason, kind="peer auth")
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429(REASON_RATE, DEFAULT_RETRY_RATE)
        finally:
            session.close()
        if plan is None:
            return _peer_404()
        headers = finalize_response_headers(plan, plan.chunk_digests)
        if is_head:
            return Response(status_code=plan.status, headers=headers)
        content_size = int(plan.binding.size)
        serve_ruche_id = plan.binding.ruche_id
        if grant_id is not None and parallel_gate is not None:

            def _stream_body():
                stream_sent = 0
                try:
                    for chunk in iter_file_byte_range(plan.binding.path, plan.byte_range):
                        parallel_gate.record_grant_bytes(grant_id, len(chunk))
                        stream_sent += len(chunk)
                        yield chunk
                finally:
                    inflight = parallel_gate.grant_inflight_count(grant_id)
                    sent = parallel_gate.grant_bytes_sent(grant_id)
                    parallel_gate.release(grant_id)
                    if record_quota_bytes:
                        persist_inbound_quota_bytes(
                            request.app,
                            ruche_id=serve_ruche_id,
                            requester_id=requester_id,
                            nbytes=stream_sent,
                            now=_now(),
                        )
                    if inflight == 1:
                        _maybe_revoke_completed_grant(
                            request.app,
                            grant_id,
                            content_size=content_size,
                            bytes_sent=sent,
                        )

            body_iter = _stream_body()
        else:

            def _stream_body_plain():
                total = 0
                try:
                    for chunk in iter_file_byte_range(plan.binding.path, plan.byte_range):
                        total += len(chunk)
                        yield chunk
                finally:
                    if record_quota_bytes:
                        persist_inbound_quota_bytes(
                            request.app,
                            ruche_id=serve_ruche_id,
                            requester_id=requester_id,
                            nbytes=total,
                            now=_now(),
                        )

            body_iter = _stream_body_plain()
        return StreamingResponse(
            body_iter,
            status_code=plan.status,
            headers=headers,
        )

    @router.api_route(
        "/{unknown_path:path}",
        methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        include_in_schema=False,
    )
    async def hive_unknown_path(_unknown_path: str, request: Request):
        if ".." in request.url.path:
            return _peer_404()
        return _peer_404()

    app.include_router(router)

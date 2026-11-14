"""Routes pair Hive sous /hive/ (signatures, sans session)."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from starlette.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from mediatracker.hive.chunk_digest_store import ChunkDigestsNotReady
from mediatracker.hive.fetch_grant import (
    HIVE_FETCH_GRANT_HEADER,
    FetchGrantDenied,
    mint_fetch_grant,
    verify_fetch_grant,
)
from mediatracker.hive.file_serve import (
    finalize_response_headers,
    iter_file_byte_range,
    prepare_file_response,
    resolve_local_file,
)
from mediatracker.hive.member_list import latest_signed_member_list
from mediatracker.hive.peer_auth import PeerAuthDenied, PeerAuthMalformed
from mediatracker.hive.peer_gossip import (
    GOSSIP_PAGE_DEFAULT,
    entries_page_for_peer,
    ingest_signed_entries,
    record_reachability,
    version_vector_for_ruche,
)
from mediatracker.hive.peer_inbound import authenticate_join_peer, authenticate_member_peer
from mediatracker.hive.peer_join import process_peer_join_preview, process_peer_join_request
from mediatracker.hive.peer_rate_limit import PeerRateLimited
from mediatracker.hive.transfer_bandwidth import TransferBandwidthDenied
from mediatracker.hive.transfer_parallel import TransferParallelGate
from mediatracker.hive.transfer_policy import TransferQuotaExceeded, TransferWindowClosed
from mediatracker.hive.transfer_settings import transfer_parallel_limit

_MAX_PEER_BODY = 1_000_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _peer_404():
    return PlainTextResponse("Not Found", status_code=404)


def _peer_401():
    return PlainTextResponse("Unauthorized", status_code=401)


def _peer_429():
    return PlainTextResponse("Too Many Requests", status_code=429)


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
        except PeerAuthDenied:
            session.rollback()
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
        except PeerAuthDenied:
            session.rollback()
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
        except PeerAuthDenied:
            session.rollback()
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429()
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
        except PeerAuthDenied:
            session.rollback()
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429()
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
        cursor = doc.get("cursor")
        limit_raw = doc.get("limit")
        limit = GOSSIP_PAGE_DEFAULT
        if isinstance(limit_raw, int) and 1 <= limit_raw <= GOSSIP_PAGE_DEFAULT:
            limit = limit_raw
        page_cursor = cursor if isinstance(cursor, dict) else None
        is_hint = doc.get("hint") is True

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
            ingest_signed_entries(session, ruche_id=ruche_id, items=entries_in, now=now)
            our_vector = version_vector_for_ruche(session, ruche_id)
            if is_hint:
                session.commit()
                return JSONResponse(
                    {
                        "vector": our_vector,
                        "entries": [],
                        "has_more": False,
                        "cursor": None,
                    }
                )
            page = entries_page_for_peer(
                session, ruche_id=ruche_id, peer_vector=vector, limit=limit, cursor=page_cursor
            )
            session.commit()
            return JSONResponse(
                {
                    "vector": our_vector,
                    "entries": page.entries,
                    "has_more": page.has_more,
                    "cursor": page.cursor,
                }
            )
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied:
            session.rollback()
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429()
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
        except PeerAuthDenied:
            session.rollback()
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429()
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
                except FetchGrantDenied:
                    session.rollback()
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
            try:
                plan = prepare_file_response(
                    session,
                    binding=binding,
                    requester_id=requester_id,
                    range_header=range_header,
                    now=now,
                    require_chunk_digests=require_chunks,
                    record_transfer_bytes=not is_head,
                )
            except TransferQuotaExceeded:
                session.rollback()
                return _peer_429()
            except TransferWindowClosed:
                session.rollback()
                return _peer_429()
            except ChunkDigestsNotReady:
                session.rollback()
                from mediatracker.hive.fingerprint_wake import poke_fingerprint_fifo_only

                await asyncio.to_thread(poke_fingerprint_fifo_only, request.app.state.settings)
                return _peer_503()
            if grant_id is not None and not is_head:
                parallel_gate = getattr(request.app.state, "hive_transfer_parallel", None)
                if isinstance(parallel_gate, TransferParallelGate):
                    limit = transfer_parallel_limit(session)
                    if not parallel_gate.acquire(grant_id, limit):
                        session.rollback()
                        return _peer_429()
            if plan is None:
                session.rollback()
                return PlainTextResponse("Requested Range Not Satisfiable", status_code=416)
            limiter = getattr(request.app.state, "hive_send_bandwidth", None)
            if limiter is not None and not is_head:
                try:
                    limiter.reserve(requester_id, plan.byte_range.length)
                except TransferBandwidthDenied:
                    session.rollback()
                    return _peer_429()
            session.commit()
        except PeerAuthMalformed:
            session.rollback()
            return _peer_404()
        except PeerAuthDenied:
            session.rollback()
            return _peer_401()
        except PeerRateLimited:
            session.rollback()
            return _peer_429()
        finally:
            session.close()
        if plan is None:
            return _peer_404()
        headers = finalize_response_headers(plan, plan.chunk_digests)
        if is_head:
            return Response(status_code=plan.status, headers=headers)
        parallel_gate = getattr(request.app.state, "hive_transfer_parallel", None)
        if grant_id is not None and isinstance(parallel_gate, TransferParallelGate):

            def _stream_body():
                try:
                    yield from iter_file_byte_range(plan.binding.path, plan.byte_range)
                finally:
                    parallel_gate.release(grant_id)

            body_iter = _stream_body()
        else:
            body_iter = iter_file_byte_range(plan.binding.path, plan.byte_range)
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

"""Client des requêtes pair signées."""

from __future__ import annotations

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mediatracker.hive.peer_auth import sign_request_headers
from mediatracker.hive.peer_transport import peer_request


def signed_peer_request(
    *,
    base_url: str,
    method: str,
    path: str,
    ruche_id: bytes,
    private_key: Ed25519PrivateKey,
    body: bytes = b"",
    query: str = "",
) -> httpx.Response:
    headers = sign_request_headers(
        private_key=private_key,
        method=method,
        path=path,
        query=query,
        ruche_id=ruche_id,
        body=body,
    )
    url = path if not query else f"{path}?{query}"
    return peer_request(base_url=base_url, method=method, url_path=url, headers=headers, body=body)


def gossip_sync_once(
    *,
    base_url: str,
    ruche_id: bytes,
    private_key: Ed25519PrivateKey,
    vector: dict[str, int],
    entries: list | None = None,
    cursor: dict | None = None,
    limit: int | None = None,
    hint: bool = False,
    signal_vector: dict[str, int] | None = None,
    signals: list | None = None,
    signal_cursor: dict | None = None,
    load: dict | None = None,
) -> httpx.Response:
    import json

    doc: dict[str, object] = {"vector": vector, "entries": entries or []}
    if signal_vector is not None:
        doc["signal_vector"] = signal_vector
    if signals is not None:
        doc["signals"] = signals
    if signal_cursor is not None:
        doc["signal_cursor"] = signal_cursor
    if cursor is not None:
        doc["cursor"] = cursor
    if limit is not None:
        doc["limit"] = limit
    if hint:
        doc["hint"] = True
    if load is not None:
        doc["load"] = load
    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    path = f"/hive/ruche/{ruche_id.hex()}/gossip/sync"
    return signed_peer_request(
        base_url=base_url,
        method="POST",
        path=path,
        ruche_id=ruche_id,
        private_key=private_key,
        body=body,
    )


def gossip_sync(
    *,
    base_url: str,
    ruche_id: bytes,
    private_key: Ed25519PrivateKey,
    vector: dict[str, int],
    entries: list | None = None,
    load: dict | None = None,
) -> httpx.Response:
    return gossip_sync_once(
        base_url=base_url,
        ruche_id=ruche_id,
        private_key=private_key,
        vector=vector,
        entries=entries,
        load=load,
    )


def fetch_member_list(
    *,
    base_url: str,
    ruche_id: bytes,
    private_key: Ed25519PrivateKey,
) -> httpx.Response:
    path = f"/hive/ruche/{ruche_id.hex()}/member-list"
    return signed_peer_request(
        base_url=base_url,
        method="GET",
        path=path,
        ruche_id=ruche_id,
        private_key=private_key,
        body=b"",
    )

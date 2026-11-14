"""aria2 JSON-RPC client. The daemon listens on 127.0.0.1 only."""

import logging
import os
import re
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from mediatracker.providers.base import DownloadStatus, Health

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_URL = re.compile(r"https?://\S+")
_QUEUE_DETAIL_KEYS = ["gid", "status", "files"]
_TELL_PAGE_SIZE = 1000
_TELL_MAX_PAGES = 100
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Aria2QueueEntry:
    gid: str
    status: str
    filename: str | None


class Aria2Error(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


def scrub_url(text: str) -> str:
    return _URL.sub("[lien]", text)


class Aria2Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rpc_url: str = "http://127.0.0.1:6800/jsonrpc"
    rpc_secret: str = Field(default="", json_schema_extra={"secret": True})

    @field_validator("rpc_url")
    @classmethod
    def localhost_only(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme not in {"http", "https"} or host not in _LOOPBACK or parts.username or parts.password:
            raise ValueError("aria2 n'est joignable qu'en local (127.0.0.1).")
        return value.strip()


class Aria2Client:
    def __init__(self, rpc_url: str, rpc_secret: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self.rpc_url = rpc_url
        self.rpc_secret = rpc_secret
        self._client = httpx.Client(transport=transport, timeout=20.0, follow_redirects=False)

    def close(self) -> None:
        self._client.close()

    def get_version(self) -> str:
        payload = self._call("aria2.getVersion", [])
        version = payload.get("version") if isinstance(payload, dict) else None
        return str(version or "")

    def global_stat(self) -> dict[str, object]:
        payload = self._call("aria2.getGlobalStat", [])
        return payload if isinstance(payload, dict) else {}

    def change_option(self, gid: str, options: dict[str, str | list[str]]) -> None:
        self._call("aria2.changeOption", [gid, options])

    def add_uri(
        self,
        url: str,
        dest_dir: Path,
        filename: str | None,
        headers: dict[str, str] | None = None,
        extra_options: dict[str, str] | None = None,
    ) -> str:
        options: dict[str, str | list[str]] = {"dir": str(dest_dir)}
        if filename:
            options["out"] = filename
        if headers:
            options["header"] = [f"{key}: {value}" for key, value in headers.items() if value]
        if extra_options:
            for key, value in extra_options.items():
                if value is not None:
                    options[key] = value
        gid = self._call("aria2.addUri", [[url], options])
        if not isinstance(gid, str) or not gid:
            raise Aria2Error("BAD_RESPONSE", "aria2 n'a pas renvoyé d'identifiant.")
        return gid

    def tell_status(self, gid: str) -> DownloadStatus:
        info = self._call("aria2.tellStatus", [gid])
        if not isinstance(info, dict):
            raise Aria2Error("BAD_RESPONSE", "Statut aria2 illisible.")
        done = _int(info.get("completedLength"))
        total = _int(info.get("totalLength"))
        speed = _int(info.get("downloadSpeed"))
        state = str(info.get("status") or "error")
        path = None
        files = info.get("files")
        if isinstance(files, list) and files and isinstance(files[0], dict):
            raw_path = files[0].get("path")
            if isinstance(raw_path, str) and raw_path:
                path = raw_path
        error = None
        if state == "error":
            code = info.get("errorCode")
            message = str(info.get("errorMessage") or "")
            parts: list[str] = []
            if code not in (None, "", "0", 0):
                parts.append(str(code))
            if message:
                parts.append(message)
            error = scrub_url(": ".join(parts) or "erreur aria2")
        return DownloadStatus(
            state=state,
            done_bytes=done,
            total_bytes=total or None,
            speed=speed or None,
            error=error,
            path=path,
        )

    def pause_all(self) -> None:
        """Pause every download and ask aria2 to write its session file."""
        self._call("aria2.pauseAll", [])
        try:
            self.save_session()
        except Aria2Error:
            return

    def list_queue(self) -> list[Aria2QueueEntry]:
        entries: list[Aria2QueueEntry] = []
        active = self._call("aria2.tellActive", [_QUEUE_DETAIL_KEYS])
        if isinstance(active, list):
            entries.extend(self._queue_entries_from(active))
        for method in ("aria2.tellWaiting", "aria2.tellStopped"):
            entries.extend(self._queue_entries_from(self._tell_paginated(method)))
        return entries

    def drop_result(self, gid: str) -> None:
        self._call("aria2.removeDownloadResult", [gid])

    def save_session(self) -> None:
        self._call("aria2.saveSession", [])

    def remove(self, gid: str, *, delete_files: bool) -> None:
        method = "aria2.remove" if not delete_files else "aria2.forceRemove"
        try:
            self._call(method, [gid])
        except Aria2Error:
            if method != "aria2.forceRemove":
                self._call("aria2.forceRemove", [gid])
            else:
                raise
        # Neither RPC unlinks the video: the queue moves it. The stopped result must
        # leave memory either way or ``force-save`` writes the GID back into the session.
        try:
            self.drop_result(gid)
        except Aria2Error as exc:
            logger.info("aria2.removeDownloadResult ignoré pour %s : %s.", gid, exc.message)
        try:
            self.save_session()
        except Aria2Error as exc:
            logger.info("aria2.saveSession ignoré : %s.", exc.message)

    def forget_files(self, paths: list[Path]) -> list[str]:
        """Stop downloads whose incomplete file is about to leave the folder."""
        wanted = _file_targets({path.name for path in paths})
        if not wanted:
            return []
        matched: list[str] = []
        for info in self._listed():
            gid = info.get("gid")
            if not isinstance(gid, str) or not gid:
                continue
            if _download_names(info) & wanted:
                matched.append(gid)
        for gid in matched:
            self.remove(gid, delete_files=False)
        return matched

    def _listed(self) -> list[dict]:
        found: list[dict] = []
        active = self._call("aria2.tellActive", [_QUEUE_DETAIL_KEYS])
        if isinstance(active, list):
            found.extend(item for item in active if isinstance(item, dict))
        for method in ("aria2.tellWaiting", "aria2.tellStopped"):
            found.extend(item for item in self._tell_paginated(method) if isinstance(item, dict))
        return found

    def _tell_paginated(self, method: str) -> list[dict]:
        found: list[dict] = []
        offset = 0
        for _ in range(_TELL_MAX_PAGES):
            page = self._call(method, [offset, _TELL_PAGE_SIZE, _QUEUE_DETAIL_KEYS])
            if not isinstance(page, list) or not page:
                break
            found.extend(item for item in page if isinstance(item, dict))
            if len(page) < _TELL_PAGE_SIZE:
                break
            offset += _TELL_PAGE_SIZE
        return found

    def _queue_entries_from(self, items: list) -> list[Aria2QueueEntry]:
        entries: list[Aria2QueueEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            gid = item.get("gid")
            if not isinstance(gid, str) or not gid:
                continue
            status = str(item.get("status") or "unknown")
            filename = _output_filename(item)
            entries.append(Aria2QueueEntry(gid=gid, status=status, filename=filename))
        return entries

    def _call(self, method: str, params: list) -> object:
        # The secret is the first RPC parameter and must not be logged.
        body = {
            "jsonrpc": "2.0",
            "id": "mt",
            "method": method,
            "params": [f"token:{self.rpc_secret}", *params],
        }
        try:
            response = self._client.post(self.rpc_url, json=body)
        except httpx.HTTPError as exc:
            raise Aria2Error("UNREACHABLE", "aria2 est injoignable.") from exc
        if response.is_redirect:
            raise Aria2Error("REDIRECT_BLOCKED", "Redirection aria2 refusée.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise Aria2Error("BAD_RESPONSE", "Réponse aria2 illisible.") from exc
        if not isinstance(payload, dict):
            raise Aria2Error("BAD_RESPONSE", "Réponse aria2 illisible.")
        if payload.get("error"):
            message = payload["error"].get("message") if isinstance(payload["error"], dict) else "erreur aria2"
            raise Aria2Error("RPC", scrub_url(str(message))[:300])
        return payload.get("result")


def _int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _output_filename(info: dict) -> str | None:
    files = info.get("files")
    if not isinstance(files, list) or not files:
        return None
    first = files[0]
    if not isinstance(first, dict):
        return None
    for key in ("path", "relativePath"):
        raw = first.get(key)
        if isinstance(raw, str) and raw:
            return Path(raw).name
    return None


class Aria2Downloader:
    kind = "downloader"
    type = "aria2"
    capabilities = frozenset({"http"})

    def __init__(self, config: Aria2Config, *, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.client = Aria2Client(config.rpc_url, config.rpc_secret, transport=transport)

    def close(self) -> None:
        self.client.close()

    @classmethod
    # Quoted: the class attribute ``type`` would shadow the builtin in this body.
    def config_model(cls) -> "type[BaseModel]":
        return Aria2Config

    def health(self) -> Health:
        checked_at = datetime.now(timezone.utc)
        if not self.config.rpc_secret.strip():
            return Health(
                status="down",
                code="NOT_CONFIGURED",
                message="Secret RPC aria2 manquant.",
                checked_at=checked_at,
            )
        try:
            version = self.client.get_version()
        except Aria2Error as exc:
            return Health(status="down", code=exc.code, message=exc.message, checked_at=checked_at)
        return Health(status="ok", code=None, message=f"aria2 {version}".strip(), checked_at=checked_at)

    def add(
        self,
        url: str,
        dest_dir: Path,
        filename: str | None,
        headers: dict[str, str] | None = None,
        extra_options: dict[str, str] | None = None,
    ) -> str:
        dest_dir.mkdir(parents=True, exist_ok=True)
        return self.client.add_uri(url, dest_dir, filename, headers=headers, extra_options=extra_options)

    def change_option(self, gid: str, options: dict[str, str | list[str]]) -> None:
        self.client.change_option(gid, options)

    def status(self, handle: str) -> DownloadStatus:
        info = self.client.tell_status(handle)
        mapped = {
            "active": "downloading",
            "waiting": "queued",
            "paused": "queued",
            "complete": "complete",
            "error": "error",
            "removed": "removed",
        }.get(info.state, "error")
        return info.model_copy(update={"state": mapped})

    def pause(self, handle: str) -> None:
        self.client._call("aria2.pause", [handle])

    def pause_all(self) -> None:
        self.client.pause_all()

    def resume(self, handle: str) -> None:
        self.client._call("aria2.unpause", [handle])

    def remove(self, handle: str, delete_files: bool = False) -> None:
        self.client.remove(handle, delete_files=delete_files)

    def forget_files(self, paths: list[Path]) -> list[str]:
        return self.client.forget_files(paths)

    def list_queue(self) -> list[Aria2QueueEntry]:
        return self.client.list_queue()

    def purge_orphan(self, gid: str, *, active: bool) -> None:
        if active:
            self.client.remove(gid, delete_files=False)
        else:
            try:
                self.client.drop_result(gid)
            except Aria2Error:
                self.client.remove(gid, delete_files=False)


def drop_session_downloads(path: Path, names: set[str]) -> int:
    """Remove session blocks whose ``out`` name is leaving the incomplete folder."""
    targets = _file_targets(names)
    if not targets or not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    kept: list[list[str]] = []
    dropped = 0
    for block in _session_blocks(text):
        if _session_matches(block, targets):
            dropped += 1
            continue
        kept.append(block)
    if not dropped:
        return 0
    body = "\n".join("\n".join(block) for block in kept)
    if body:
        body += "\n"
    _write_session_file(path, body)
    return dropped


def _download_names(info: dict) -> set[str]:
    names: set[str] = set()
    files = info.get("files")
    if isinstance(files, list):
        for item in files:
            if not isinstance(item, dict):
                continue
            raw = item.get("path")
            if isinstance(raw, str) and raw:
                names.add(Path(raw).name)
    return names


def _session_matches(block: list[str], targets: set[str]) -> bool:
    output: str | None = None
    for line in block:
        stripped = line.strip()
        if stripped.startswith("out="):
            output = Path(stripped.split("=", 1)[1].strip()).name
            break
    if output is not None:
        return output in targets
    for line in block:
        if not line.strip() or line[0] in " \t" or line.startswith("#"):
            continue
        if Path(urlsplit(line.strip()).path).name in targets:
            return True
    return False


def session_name_targets(names: Iterable[str]) -> set[str]:
    return _file_targets({name for name in names if name})


def filter_aria2_session_file(path: Path, *, keep_gids: set[str], keep_names: set[str]) -> int:
    """Keep only session blocks that match an active download GID or output name.

    Returns how many blocks were removed. The file is left untouched when nothing
    changes or when it is missing.
    """
    if not path.is_file():
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    targets = session_name_targets(keep_names)
    kept: list[list[str]] = []
    dropped = 0
    for block in _session_blocks(text):
        if _session_block_active(block, keep_gids, targets):
            kept.append(block)
        else:
            dropped += 1
    if not dropped:
        return 0
    body = "\n".join("\n".join(block) for block in kept)
    if body:
        body += "\n"
    _write_session_file(path, body)
    return dropped


def _write_session_file(path: Path, body: str) -> None:
    backup = path.with_name(f"{path.name}.bak")
    shutil.copy2(path, backup)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(body, encoding="utf-8")
    try:
        os.chmod(temporary, path.stat().st_mode & 0o777)
    except OSError:
        pass
    os.replace(temporary, path)


def _file_targets(names: set[str]) -> set[str]:
    found: set[str] = set()
    for name in names:
        if not name or name in {".", ".."}:
            continue
        found.add(name)
        if name.endswith(".aria2"):
            found.add(name[: -len(".aria2")])
    return found


def _session_blocks(text: str) -> list[list[str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            if current:
                blocks.append(current)
                current = []
            continue
        if line[0] in " \t":
            current.append(line)
            continue
        if current and current[-1][0] in " \t":
            blocks.append(current)
            current = [line]
            continue
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _session_block_active(block: list[str], keep_gids: set[str], keep_names: set[str]) -> bool:
    gid, names = _session_block_keys(block)
    if gid and gid in keep_gids:
        return True
    return bool(names & keep_names)


def _session_block_keys(block: list[str]) -> tuple[str | None, set[str]]:
    gid: str | None = None
    names: set[str] = set()
    for line in block:
        stripped = line.strip()
        if stripped.startswith("gid="):
            gid = stripped.split("=", 1)[1].strip()
        elif stripped.startswith("out="):
            names |= _file_targets({Path(stripped.split("=", 1)[1].strip()).name})
    for line in block:
        if not line.strip() or line[0] in " \t" or line.startswith("#"):
            continue
        names |= _file_targets({Path(urlsplit(line.strip()).path).name})
    return gid, names

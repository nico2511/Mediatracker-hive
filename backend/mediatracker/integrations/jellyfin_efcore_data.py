"""Read Jellyfin 10.10+ EF Core data from SQLite or a 10.11 backup ZIP (Database/*.json)."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SQLITE_MAGIC = b"SQLite format 3\x00"

PLACEHOLDER_ITEM_ID = "00000000-0000-0000-0000-000000000001"
BACKUP_ZIP_NAME = "jellyfin-backup.zip"
MAX_BACKUP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class JellyfinExportError(Exception):
    """Invalid Jellyfin database or export parameters."""


def verify_sqlite_file(path: Path) -> None:
    if not path.is_file():
        raise JellyfinExportError("Fichier introuvable.")
    with path.open("rb") as handle:
        if handle.read(len(SQLITE_MAGIC)) != SQLITE_MAGIC:
            raise JellyfinExportError("Le fichier n'est pas une base SQLite valide.")


def normalize_guid(value: str | None) -> str:
    if not value:
        return ""
    text = str(value).strip()
    if _HEX32_RE.fullmatch(text):
        text = f"{text[0:8]}-{text[8:12]}-{text[12:16]}-{text[16:20]}-{text[20:32]}"
    return text.lower()


def normalize_jellyfin_id(value: str | None) -> str:
    """Jellyfin API id format: 32 lowercase hex digits (no dashes)."""
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    if guids_equal(text, PLACEHOLDER_ITEM_ID):
        return ""
    try:
        return uuid.UUID(text).hex
    except ValueError:
        dashed = normalize_guid(text)
        if dashed:
            try:
                return uuid.UUID(dashed).hex
            except ValueError:
                return ""
        if _HEX32_RE.fullmatch(text):
            return text.lower()
        return ""


def guids_equal(a: str | None, b: str | None) -> bool:
    na, nb = normalize_guid(a), normalize_guid(b)
    return bool(na) and na == nb


def is_backup_database_json_path(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if ".." in normalized.split("/"):
        return False
    if normalized.count("/") != 1:
        return False
    left, right = normalized.split("/", 1)
    return left == "Database" and right.endswith(".json")


def add_backup_uncompressed_bytes(total: int, add: int) -> int:
    total += add
    if total > MAX_BACKUP_UNCOMPRESSED_BYTES:
        raise JellyfinExportError("Archive de sauvegarde trop volumineuse.")
    return total


@dataclass(frozen=True)
class ParsedCustomKey:
    kind: str
    guid: str | None = None
    tmdb_id: int | None = None
    imdb_id: str | None = None
    tvdb_id: int | None = None
    season: int | None = None
    episode: int | None = None
    series_guid: str | None = None


def parse_custom_data_key(key: str) -> ParsedCustomKey | None:
    raw = str(key or "").strip()
    if not raw:
        return None
    if raw.startswith("Person-"):
        return None
    if _GUID_RE.fullmatch(raw):
        return ParsedCustomKey(kind="guid", guid=normalize_guid(raw))
    if len(raw) > 36 and _GUID_RE.fullmatch(raw[:36]):
        suffix = raw[36:]
        ssseee = suffix[-6:] if len(suffix) >= 6 and suffix[-6:].isdigit() else None
        if ssseee:
            return ParsedCustomKey(
                kind="series_guid_episode",
                series_guid=normalize_guid(raw[:36]),
                season=int(ssseee[:3]),
                episode=int(ssseee[3:]),
            )
        return ParsedCustomKey(kind="guid", guid=normalize_guid(raw[:36]))
    if raw.startswith("tt"):
        digits = raw[2:]
        if len(digits) >= 10 and digits[-6:].isdigit():
            series = raw[: len(raw) - 6]
            ssseee = digits[-6:]
            return ParsedCustomKey(
                kind="imdb_episode",
                imdb_id=series,
                season=int(ssseee[:3]),
                episode=int(ssseee[3:]),
            )
        if len(raw) >= 9:
            return ParsedCustomKey(kind="imdb_film", imdb_id=raw)
        return None
    if raw.isdigit():
        if len(raw) <= 7:
            return ParsedCustomKey(kind="tmdb_film", tmdb_id=int(raw))
        if 8 <= len(raw) <= 10:
            return None
        if len(raw) in {11, 12}:
            ssseee = raw[-6:]
            tvdb_part = raw[:-6]
            if tvdb_part.isdigit():
                return ParsedCustomKey(
                    kind="tvdb_episode",
                    tvdb_id=int(tvdb_part),
                    season=int(ssseee[:3]),
                    episode=int(ssseee[3:]),
                )
        return None
    return None


def media_cluster_key(parsed: ParsedCustomKey) -> tuple:
    if parsed.kind == "tmdb_film" and parsed.tmdb_id is not None:
        return ("movie", "tmdb", parsed.tmdb_id)
    if parsed.kind == "imdb_film" and parsed.imdb_id:
        return ("movie", "imdb", parsed.imdb_id)
    if parsed.kind == "tvdb_episode" and parsed.tvdb_id is not None:
        return ("episode", "tvdb", parsed.tvdb_id, parsed.season, parsed.episode)
    if parsed.kind == "imdb_episode" and parsed.imdb_id:
        return ("episode", "imdb", parsed.imdb_id, parsed.season, parsed.episode)
    if parsed.kind == "series_guid_episode" and parsed.series_guid:
        return ("episode", "sg", parsed.series_guid, parsed.season, parsed.episode)
    if parsed.guid:
        return ("guid", parsed.guid)
    return ("unknown", parsed.kind)


def load_backup_zip_tables(path: Path) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {}
    total = 0
    try:
        zf = zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as exc:
        raise JellyfinExportError("Archive ZIP invalide ou corrompue.") from exc
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if not is_backup_database_json_path(name):
                continue
            table = Path(name).name.removesuffix(".json")
            try:
                raw = zf.read(info)
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error) as exc:
                raise JellyfinExportError("Archive ZIP : compression ou chiffrement non pris en charge.") from exc
            total = add_backup_uncompressed_bytes(total, len(raw))
            try:
                payload = json.loads(raw.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise JellyfinExportError("Archive ZIP : encodage UTF-8 invalide dans Database/*.json.") from exc
            except json.JSONDecodeError as exc:
                raise JellyfinExportError(f"Archive ZIP : JSON invalide ({table}).") from exc
            if not isinstance(payload, list):
                raise JellyfinExportError(f"Table JSON invalide : {table}.")
            tables[table] = payload
    for required in ("Users", "UserData", "BaseItems"):
        if required not in tables:
            raise JellyfinExportError(
                f"Sauvegarde Jellyfin incomplète : table {required} absente dans Database/."
            )
    return tables


class EfCoreStore:
    def list_users(self) -> list[tuple[str, str]]:
        raise NotImplementedError

    def fetch_items(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_providers(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_userdata(self, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_activity_logs(self, user_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def close(self) -> None:
        return None


class SqliteEfCoreStore(EfCoreStore):
    def __init__(self, path: Path) -> None:
        verify_sqlite_file(path)
        self._conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
        self._conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self._conn.close()

    def _tables(self) -> set[str]:
        rows = self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {str(r[0]) for r in rows}

    def list_users(self) -> list[tuple[str, str]]:
        if "Users" not in self._tables():
            raise JellyfinExportError("Aucun utilisateur dans jellyfin.db.")
        rows = self._conn.execute("SELECT Id, Username FROM Users").fetchall()
        if not rows:
            raise JellyfinExportError("Aucun utilisateur dans jellyfin.db.")
        return [(str(r["Id"]), str(r["Username"] or "")) for r in rows]

    def fetch_items(self) -> list[dict[str, Any]]:
        query_with_ticks = (
            "SELECT Id, Type, Name, ProductionYear, SeriesName, ParentIndexNumber, IndexNumber, "
            "SeriesId, IsMovie, RunTimeTicks FROM BaseItems"
        )
        query_plain = (
            "SELECT Id, Type, Name, ProductionYear, SeriesName, ParentIndexNumber, IndexNumber, "
            "SeriesId, IsMovie FROM BaseItems"
        )
        try:
            rows = self._conn.execute(query_with_ticks).fetchall()
        except sqlite3.OperationalError:
            rows = self._conn.execute(query_plain).fetchall()
        return [dict(r) for r in rows]

    def fetch_providers(self) -> list[dict[str, Any]]:
        if "BaseItemProviders" not in self._tables():
            return []
        rows = self._conn.execute("SELECT ItemId, ProviderId, ProviderValue FROM BaseItemProviders").fetchall()
        return [dict(r) for r in rows]

    def fetch_userdata(self, user_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT ItemId, UserId, CustomDataKey, Played, PlayCount, LastPlayedDate FROM UserData "
            "WHERE UserId = ?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def fetch_activity_logs(self, user_id: str) -> list[dict[str, Any]]:
        if "ActivityLogs" not in self._tables():
            return []
        rows = self._conn.execute(
            "SELECT Type, Name, UserId, ItemId, DateCreated FROM ActivityLogs WHERE UserId = ?",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


class BackupZipEfCoreStore(EfCoreStore):
    def __init__(self, path: Path) -> None:
        if not path.is_file():
            raise JellyfinExportError("Archive de sauvegarde introuvable.")
        self._tables = load_backup_zip_tables(path)

    def list_users(self) -> list[tuple[str, str]]:
        rows = self._tables.get("Users", [])
        if not rows:
            raise JellyfinExportError("Aucun utilisateur dans la sauvegarde Jellyfin.")
        return [(str(r.get("Id", "")), str(r.get("Username") or "")) for r in rows]

    def fetch_items(self) -> list[dict[str, Any]]:
        return list(self._tables.get("BaseItems", []))

    def fetch_providers(self) -> list[dict[str, Any]]:
        return list(self._tables.get("BaseItemProviders", []))

    def fetch_userdata(self, user_id: str) -> list[dict[str, Any]]:
        uid = normalize_guid(user_id)
        out: list[dict[str, Any]] = []
        for row in self._tables.get("UserData", []):
            if guids_equal(str(row.get("UserId")), uid):
                out.append(row)
        return out

    def fetch_activity_logs(self, user_id: str) -> list[dict[str, Any]]:
        uid = normalize_guid(user_id)
        out: list[dict[str, Any]] = []
        for row in self._tables.get("ActivityLogs", []):
            if guids_equal(str(row.get("UserId")), uid):
                out.append(row)
        return out


def open_efcore_store(*, jellyfin_db: Path | None = None, backup_zip: Path | None = None) -> EfCoreStore:
    if jellyfin_db is not None and jellyfin_db.is_file():
        return SqliteEfCoreStore(jellyfin_db)
    if backup_zip is not None and backup_zip.is_file():
        return BackupZipEfCoreStore(backup_zip)
    raise JellyfinExportError("Aucune source Jellyfin valide.")

"""Jellyfin client and the mapping from a Jellyfin item to one of our episodes.

Watched state comes from the webhook plugin and from a periodic poll. Library
refresh is a separate debounced call. No CDN or API key is logged.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from mediatracker.security.logging import redact
from mediatracker.security.ssrf import OutboundUrlRejected, httpx_request_event_hooks, validate_outbound_url_static

logger = logging.getLogger(__name__)

_WATCHED_EVENTS = frozenset({"PlaybackStop", "UserDataSaved"})
_ITEM_TYPES = frozenset({"Episode", "Movie"})
# ItemFields on Jellyfin 10.10/10.11. SeriesId, SeriesName and ProductionYear are
# always on the item; sending them as Fields makes the server return 400.
_ITEM_FIELDS = "ProviderIds,Path,ParentId"
_EXCERPT = 300


class JellyfinError(Exception):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class JellyfinWatch:
    item_id: str
    item_type: str
    tmdb_id: int | None
    season: int | None
    episode: int | None
    path: str | None
    played: bool
    user_id: str | None = None
    tvdb_id: int | None = None
    series_tmdb_id: int | None = None
    episode_tmdb_id: int | None = None
    series_id: str | None = None
    series_name: str | None = None
    series_year: int | None = None
    imdb_id: str | None = None
    played_percent: int | None = None
    series_original_name: str | None = None
    last_played_at: datetime | None = None
    playback_event: bool = False


class JellyfinClient:
    def __init__(
        self,
        base_url: str | None,
        api_key: str | None,
        user_id: str | None,
        *,
        timeout: float = 20.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        clean = (base_url or "").rstrip("/") or None
        if clean:
            try:
                validate_outbound_url_static(clean)
            except OutboundUrlRejected as exc:
                raise JellyfinError("SSRF", str(exc)) from exc
        self.base_url = clean
        self.api_key = api_key or None
        self.user_id = user_id or None
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = (
                'MediaBrowser Client="MediaTracker", Device="NAS", DeviceId="mediatracker", '
                f'Version="2.0.0", Token="{self.api_key}"'
            )
        from mediatracker.core.outbound_http import borrow_http_client

        self._client, self._owns_client = borrow_http_client(
            ("jellyfin", self.base_url or "", self.api_key or ""),
            httpx.Client,
            transport=transport,
            base_url=self.base_url or "",
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            event_hooks=httpx_request_event_hooks(),
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def system_info(self) -> dict:
        payload = self._request("GET", "/System/Info")
        return payload if isinstance(payload, dict) else {}

    def users(self) -> list[dict]:
        payload = self._request("GET", "/Users")
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def virtual_folders(self) -> list[dict]:
        payload = self._request("GET", "/Library/VirtualFolders")
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def mark_played(self, user_id: str, item_id: str) -> None:
        self._request("POST", f"/Users/{user_id}/PlayedItems/{item_id}")

    def mark_unplayed(self, user_id: str, item_id: str) -> None:
        self._request("DELETE", f"/Users/{user_id}/PlayedItems/{item_id}")

    def refresh_library(self) -> None:
        """Ask Jellyfin to rescan. Debounced by the caller."""
        self._request("POST", "/Library/Refresh")

    def scheduled_tasks(self) -> list[dict]:
        payload = self._request("GET", "/ScheduledTasks")
        if not isinstance(payload, list):
            return []
        return [row for row in payload if isinstance(row, dict)]

    def refresh_item(self, item_id: str, *, replace_metadata: bool = False) -> None:
        """Refresh one item. ``replace_metadata`` re-reads the providers from scratch."""
        mode = "FullRefresh" if replace_metadata else "Default"
        self._request(
            "POST",
            f"/Items/{item_id}/Refresh",
            params={
                "Recursive": "true",
                "MetadataRefreshMode": mode,
                "ImageRefreshMode": "Default",
                "ReplaceAllMetadata": "true" if replace_metadata else "false",
                "ReplaceAllImages": "false",
            },
        )

    def apply_remote_search(self, item_id: str, result: dict) -> None:
        """Identify an item from a provider result. Images already on disk stay."""
        self._request(
            "POST",
            f"/Items/RemoteSearch/Apply/{item_id}",
            params={"replaceAllImages": "false"},
            json_body=result,
        )

    def update_item(self, item_id: str, payload: dict) -> None:
        """Write provider ids when RemoteSearch/Apply is refused."""
        body = dict(payload)
        body.setdefault("Id", item_id)
        self._request("POST", f"/Items/{item_id}", json_body=body)

    def notify_updated(self, paths: list[str], update_type: str = "Created") -> None:
        """Targeted scan of the paths that just moved. ``update_type`` is Created, Modified, or Deleted."""
        self._request(
            "POST",
            "/Library/Media/Updated",
            json_body={"Updates": [{"Path": path, "UpdateType": update_type} for path in paths if path]},
        )

    def catalog_items(self, user_id: str, *, since: str | None = None) -> list[dict]:
        """Series, seasons, episodes and movies. ``since`` is MinDateLastSaved for an incremental read."""
        extra = {"MinDateLastSaved": since} if since else None
        types = "Series,Season,Episode,Movie"
        try:
            return self._collect_pages(user_id, types, "ProviderIds,Path,ParentId,DateLastSaved", extra)
        except JellyfinError as exc:
            if exc.code != "BAD_RESPONSE":
                raise
            logger.info("Catalogue Jellyfin refusé, nouvel essai sans DateLastSaved.")
        try:
            return self._collect_pages(user_id, types, "ProviderIds,Path,ParentId", extra)
        except JellyfinError as exc:
            if exc.code != "BAD_RESPONSE":
                raise
            logger.info("Catalogue Jellyfin refusé, lecture sans Fields.")
            return self._collect_pages(user_id, types, None, extra)

    def search_series(self, user_id: str, term: str) -> list[dict]:
        """Series whose name matches. The caller passes a resolved user GUID."""
        payload = self._request(
            "GET",
            f"/Users/{user_id}/Items",
            params={
                "SearchTerm": term,
                "IncludeItemTypes": "Series",
                "Recursive": "true",
                "Limit": "20",
                "Fields": "ProviderIds,ProductionYear,OriginalTitle",
            },
        )
        if not isinstance(payload, dict):
            return []
        return [row for row in (payload.get("Items") or []) if isinstance(row, dict)]

    def user_items(self, user_id: str) -> list[dict]:
        return self._pages(user_id, "Movie,Episode")

    def series_index(self, user_id: str) -> dict[str, dict]:
        """Parent series rows, keyed by Jellyfin id, so an episode can use the series ProviderIds."""
        found: dict[str, dict] = {}
        for row in self._pages(user_id, "Series"):
            item_id = row.get("Id")
            if isinstance(item_id, str) and item_id:
                found[item_id] = row
        return found

    def _pages(self, user_id: str, item_types: str) -> list[dict]:
        """Valid Fields first. A 400 falls back to ProviderIds, then to name and year only."""
        try:
            return self._collect_pages(user_id, item_types, _ITEM_FIELDS)
        except JellyfinError as exc:
            if exc.code != "BAD_RESPONSE":
                raise
            logger.info("Liste Jellyfin refusée, nouvel essai avec ProviderIds seuls.")
        try:
            return self._collect_pages(user_id, item_types, "ProviderIds")
        except JellyfinError as exc:
            if exc.code != "BAD_RESPONSE":
                raise
            logger.info("Liste Jellyfin refusée, lecture du nom et de l'année sans Fields.")
            return self._collect_pages(user_id, item_types, None)

    def _collect_pages(
        self,
        user_id: str,
        item_types: str,
        fields: str | None,
        extra: dict[str, str] | None = None,
    ) -> list[dict]:
        start = 0
        limit = 200
        collected: list[dict] = []
        while True:
            params = {
                "Recursive": "true",
                "IncludeItemTypes": item_types,
                "EnableUserData": "true",
                "StartIndex": str(start),
                "Limit": str(limit),
            }
            if fields:
                params["Fields"] = fields
            if extra:
                params.update(extra)
            payload = self._request("GET", f"/Users/{user_id}/Items", params=params)
            if not isinstance(payload, dict):
                break
            batch = [row for row in (payload.get("Items") or []) if isinstance(row, dict)]
            collected.extend(batch)
            total = payload.get("TotalRecordCount")
            start += len(batch)
            if not batch or len(batch) < limit:
                break
            if isinstance(total, int) and start >= total:
                break
        return collected

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None = None,
        json_body: dict | None = None,
    ) -> dict | list:
        if not self.base_url or not self.api_key:
            raise JellyfinError("NOT_CONFIGURED", "Jellyfin n'est pas configuré.")
        try:
            response = self._client.request(method, path, params=params, json=json_body)
        except OutboundUrlRejected as exc:
            raise JellyfinError("SSRF", str(exc)) from exc
        except httpx.HTTPError as exc:
            raise JellyfinError("TRANSPORT", "Jellyfin est injoignable.") from exc
        if response.status_code in {401, 403}:
            raise JellyfinError("UNAUTHORIZED", "Jellyfin a refusé la clé API.")
        if response.status_code >= 400:
            excerpt = _excerpt(response.text)
            logger.warning(
                "Jellyfin %s %s a répondu %s. %s",
                method,
                path,
                response.status_code,
                excerpt,
            )
            detail = f"Jellyfin a répondu {response.status_code}."
            if excerpt:
                detail = f"{detail} {excerpt}"
            raise JellyfinError("BAD_RESPONSE", detail)
        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise JellyfinError("BAD_RESPONSE", "Réponse Jellyfin illisible.") from exc
        if not isinstance(payload, dict | list):
            raise JellyfinError("BAD_RESPONSE", "Réponse Jellyfin inattendue.")
        return payload


def canonical_user_guid(value: str) -> str | None:
    """Jellyfin user ids are GUIDs. A display name does not parse."""
    text = value.strip()
    if len(text) >= 2 and text[0] in "{(" and text[-1] in "})":
        text = text[1:-1].strip()
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return None


def match_jellyfin_user(users: list[dict], configured: str | None) -> tuple[str, str | None]:
    """Resolve a settings value to ``(id, name)``.

    A GUID is kept. Anything else is matched on ``Name``, case-insensitively.
    """
    rows: list[tuple[str, str]] = []
    for row in users:
        if not isinstance(row, dict):
            continue
        raw_id = row.get("Id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        name = row.get("Name") if isinstance(row.get("Name"), str) else ""
        rows.append((raw_id.strip(), name.strip()))
    names = ", ".join(sorted({name for _user_id, name in rows if name}, key=str.casefold))
    raw = (configured or "").strip()
    if raw:
        canonical = canonical_user_guid(raw)
        if canonical:
            for user_id, name in rows:
                other = canonical_user_guid(user_id)
                if other == canonical or user_id.casefold() == canonical.casefold():
                    return user_id, name or None
            return canonical, None
        for user_id, name in rows:
            if user_id == raw:
                return user_id, name or None
        by_name = [(user_id, name) for user_id, name in rows if name.casefold() == raw.casefold()]
        if len(by_name) == 1:
            return by_name[0][0], by_name[0][1] or None
        if len(by_name) > 1:
            raise JellyfinError(
                "USER_NOT_FOUND",
                f"Plusieurs comptes Jellyfin portent le nom « {raw} ». Comptes disponibles : {names}.",
            )
        detail = f"Utilisateur Jellyfin « {raw} » introuvable."
        if names:
            detail = f"{detail} Comptes disponibles : {names}."
        raise JellyfinError("USER_NOT_FOUND", detail)
    if len(rows) == 1:
        return rows[0][0], rows[0][1] or None
    if not rows:
        raise JellyfinError("NO_USER", "Aucun utilisateur Jellyfin.")
    message = "Identifiant Jellyfin manquant."
    if names:
        message = f"{message} Comptes disponibles : {names}."
    raise JellyfinError("NO_USER", message)


def resolve_user_id(client: JellyfinClient, configured: str | None) -> str:
    """Return the user GUID, listing ``/Users`` once per client when the value is a name."""
    raw = (configured or "").strip()
    canonical = canonical_user_guid(raw)
    key = canonical or raw.casefold()
    cache = getattr(client, "_resolved_user_ids", None)
    if not isinstance(cache, dict):
        cache = {}
        client._resolved_user_ids = cache
    found = cache.get(key)
    if isinstance(found, str) and found:
        return found
    if canonical:
        cache[key] = canonical
        return canonical
    user_id, _name = match_jellyfin_user(client.users(), raw)
    cache[key] = user_id
    cache[user_id] = user_id
    return user_id


def from_api_item(raw: dict, series_by_id: dict[str, dict] | None = None) -> JellyfinWatch | None:
    item_type = str(raw.get("Type") or "")
    if item_type not in _ITEM_TYPES:
        return None
    user_data = raw.get("UserData") if isinstance(raw.get("UserData"), dict) else {}
    played, percent = _played_state(user_data, raw)
    if played is None:
        return None
    providers = raw.get("ProviderIds") if isinstance(raw.get("ProviderIds"), dict) else {}
    # SeriesId is the show. ParentId and SeasonId point at the season, not a second show.
    series_id = raw.get("SeriesId") if isinstance(raw.get("SeriesId"), str) and raw.get("SeriesId") else None
    series = (series_by_id or {}).get(series_id or "") if series_id else None
    series_providers: dict = {}
    if isinstance(series, dict) and isinstance(series.get("ProviderIds"), dict):
        series_providers = series["ProviderIds"]
    episode_tmdb = _first_int(providers.get("Tmdb"), providers.get("TMDB"))
    series_tmdb = _first_int(series_providers.get("Tmdb"), series_providers.get("TMDB"))
    if item_type == "Movie":
        series_tmdb = series_tmdb or episode_tmdb
        episode_tmdb = None
    name = None
    original = None
    year = None
    if isinstance(series, dict):
        name = series.get("Name") if isinstance(series.get("Name"), str) else None
        original = series.get("OriginalTitle") if isinstance(series.get("OriginalTitle"), str) else None
        year = _as_int(series.get("ProductionYear"))
    if name is None and isinstance(raw.get("SeriesName"), str):
        name = raw.get("SeriesName")
    imdb = _text_id(series_providers.get("Imdb") or series_providers.get("IMDB") or providers.get("Imdb"))
    tvdb = _first_int(
        series_providers.get("Tvdb"),
        series_providers.get("TVDB"),
        providers.get("Tvdb"),
        providers.get("TVDB"),
    )
    return JellyfinWatch(
        item_id=str(raw.get("Id") or ""),
        item_type=item_type,
        tmdb_id=episode_tmdb if item_type == "Episode" else series_tmdb,
        season=_first_int(raw.get("ParentIndexNumber"), raw.get("SeasonNumber")),
        episode=_first_int(raw.get("IndexNumber"), raw.get("EpisodeNumber")),
        path=raw.get("Path") if isinstance(raw.get("Path"), str) else None,
        played=played,
        tvdb_id=tvdb,
        series_tmdb_id=series_tmdb,
        episode_tmdb_id=episode_tmdb,
        series_id=series_id,
        series_name=name,
        series_year=year,
        imdb_id=imdb,
        played_percent=percent,
        series_original_name=original,
        last_played_at=_last_played_at(user_data, raw),
    )


def parse_webhook(payload: dict) -> JellyfinWatch | None:
    """Webhook plugin payload (flat fields) or a nested ``Item`` object.

    ``PlaybackStop`` marks watched only when playback finished. ``UserDataSaved``
    applies both watched and not watched. Other events are ignored.
    """
    kind = str(payload.get("NotificationType") or payload.get("Event") or "")
    if kind not in _WATCHED_EVENTS:
        return None
    item = payload.get("Item") if isinstance(payload.get("Item"), dict) else {}
    item_type = str(payload.get("ItemType") or item.get("Type") or "")
    if item_type not in _ITEM_TYPES:
        return None
    played = _as_bool(payload.get("Played"))
    completed = _as_bool(payload.get("PlayedToCompletion"))
    user_data = payload.get("UserData") if isinstance(payload.get("UserData"), dict) else {}
    if not user_data and isinstance(item.get("UserData"), dict):
        user_data = item["UserData"]
    if played is None:
        played = _as_bool(user_data.get("Played"))
    playback_event = False
    if kind == "PlaybackStop":
        if completed is not True and played is not True:
            return None
        played = True
        playback_event = True
    elif played is None:
        return None
    providers = item.get("ProviderIds") if isinstance(item.get("ProviderIds"), dict) else {}
    tmdb = _first_int(payload.get("Provider_tmdb"), payload.get("Provider_Tmdb"), providers.get("Tmdb"))
    season = _first_int(payload.get("SeasonNumber"), payload.get("ParentIndexNumber"), item.get("ParentIndexNumber"))
    episode = _first_int(payload.get("EpisodeNumber"), payload.get("IndexNumber"), item.get("IndexNumber"))
    series_name = payload.get("SeriesName") if isinstance(payload.get("SeriesName"), str) else None
    if series_name is None and isinstance(item.get("SeriesName"), str):
        series_name = item.get("SeriesName")
    series_year = _first_int(payload.get("Year"), payload.get("ProductionYear"), item.get("ProductionYear"))
    imdb = _text_id(payload.get("Provider_imdb") or providers.get("Imdb"))
    tvdb = _first_int(payload.get("Provider_tvdb"), providers.get("Tvdb"))
    path = payload.get("Path") if isinstance(payload.get("Path"), str) else None
    if path is None and isinstance(item.get("Path"), str):
        path = item["Path"]
    item_id = str(payload.get("ItemId") or item.get("Id") or "")
    series_id = payload.get("SeriesId") if isinstance(payload.get("SeriesId"), str) else None
    if series_id is None and isinstance(item.get("SeriesId"), str):
        series_id = item.get("SeriesId")
    user_id = payload.get("UserId") if isinstance(payload.get("UserId"), str) else None
    if user_id is None and isinstance(payload.get("User"), dict):
        raw_user = payload["User"].get("Id")
        user_id = raw_user if isinstance(raw_user, str) else None
    return JellyfinWatch(
        item_id=item_id,
        item_type=item_type,
        tmdb_id=tmdb,
        season=season,
        episode=episode,
        path=path,
        played=played,
        user_id=user_id,
        tvdb_id=tvdb,
        series_id=series_id,
        series_name=series_name,
        series_year=series_year,
        imdb_id=imdb,
        episode_tmdb_id=tmdb if item_type == "Episode" else None,
        series_tmdb_id=tmdb if item_type == "Movie" else None,
        last_played_at=_last_played_at(user_data, item, payload),
        playback_event=playback_event,
    )


def paths_match(
    local: str | None,
    remote: str | None,
    prefix: str | None,
    mappings: list[tuple[str, str]] | None = None,
) -> bool:
    if not local or not remote:
        return False
    left = _norm_path(local)
    right = _norm_path(remote)
    if left == right:
        return True
    if mappings:
        from mediatracker.integrations.paths import translate_path

        translated = translate_path(right, mappings)
        if translated is not None:
            return _norm_path(translated) == left
    if prefix:
        root = _norm_path(prefix).rstrip("/")
        if root and right.startswith(root + "/"):
            suffix = right[len(root) :]
            if left.endswith(suffix):
                return True
    left_parts = [part for part in left.split("/") if part]
    right_parts = [part for part in right.split("/") if part]
    if len(left_parts) < 3 or len(right_parts) < 3:
        return False
    return left_parts[-3:] == right_parts[-3:]


def _excerpt(body: str) -> str:
    """Short public fragment of a Jellyfin error. Tokens and keys stay out of logs and the UI."""
    text = redact(body or "").replace("\n", " ").strip()
    if len(text) <= _EXCERPT:
        return text
    return text[:_EXCERPT] + "…"


def _norm_path(value: str) -> str:
    text = unicodedata.normalize("NFC", value.replace("\\", "/")).rstrip("/")
    while "//" in text:
        text = text.replace("//", "/")
    return text


def parse_jellyfin_datetime(value: object) -> datetime | None:
    """Jellyfin timestamps use a trailing Z and more than six fractional digits."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, tail = text.split(".", 1)
        digits: list[str] = []
        rest = ""
        for index, char in enumerate(tail):
            if char.isdigit() and len(digits) < 6:
                digits.append(char)
                continue
            if char.isdigit():
                continue
            rest = tail[index:]
            break
        text = f"{head}.{''.join(digits)}{rest}" if digits else f"{head}{rest}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _last_played_at(*sources: dict | None) -> datetime | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in ("LastPlayedDate", "DatePlayed"):
            parsed = parse_jellyfin_datetime(source.get(key))
            if parsed is not None:
                return parsed
    return None


def _played_state(user_data: dict, raw: dict) -> tuple[bool | None, int | None]:
    """``Played`` / ``IsPlayed`` win. A percentage of 90 or more still counts as watched."""
    percent = None
    for source in (user_data, raw):
        value = source.get("PlayedPercentage")
        if isinstance(value, bool) or not isinstance(value, int | float):
            continue
        percent = int(value)
        break
    flag = None
    for key in ("Played", "IsPlayed"):
        parsed = _as_bool(user_data.get(key))
        if parsed is None:
            parsed = _as_bool(raw.get(key))
        if parsed is not None:
            flag = parsed
            break
    if flag is None:
        count = _as_int(user_data.get("PlayCount"))
        if count is not None:
            flag = count > 0
    if flag is True or (percent is not None and percent >= 90):
        return True, percent
    if flag is False:
        return False, percent
    return None, percent


def _text_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first_int(*values: object) -> int | None:
    for value in values:
        parsed = _as_int(value)
        if parsed is not None:
            return parsed
    return None

"""Compare this image with the published one. Nothing here pulls or recreates a container.

Watchtower (or a manual pull) applies the image. The registry token and the
GitHub token stay in the encrypted settings and are sent only as headers.
"""

import json
import logging
import os
import re
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from mediatracker.config import Settings
from mediatracker.db.models import Setting
from mediatracker.events import record_event
from mediatracker.security.crypto import decrypt_secret
from mediatracker.security.logging import redact

logger = logging.getLogger(__name__)
_USERINFO = re.compile(r"(://)[^/\s:@]+:[^/\s@]+@")

DEFAULT_UPDATE_IMAGE = "ghcr.io/nico2511/mediatracker-hive"
_ACCEPT = ", ".join(
    (
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    )
)
_REVISION = "org.opencontainers.image.revision"
_KEY_AVAILABLE = "system.update_available"
_KEY_REMOTE = "system.update_remote"
_KEY_DETAIL = "system.update_detail"
_KEY_CHECKED = "system.update_checked_at"


class UpdateError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class UpdateStatus:
    available: bool
    current_sha: str | None
    remote_sha: str | None
    build_date: str | None
    detail: str
    checked_at: datetime | None = None


def update_image() -> str:
    raw = (os.environ.get("MEDIATRACKER_UPDATE_IMAGE") or DEFAULT_UPDATE_IMAGE).strip()
    return raw or DEFAULT_UPDATE_IMAGE


def _parse_image_ref() -> tuple[str, str]:
    ref = update_image()
    if "@" in ref:
        ref = ref.split("@", 1)[0]
    slash = ref.rfind("/")
    if slash != -1 and ":" in ref[slash:]:
        ref = ref[: ref.rfind(":")]
    path = ref.removeprefix("ghcr.io/").lstrip("/")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise UpdateError("Image de mise à jour invalide.")
    return parts[0], parts[1]


def _github_repo_for_image() -> str:
    owner, repo = _parse_image_ref()
    if repo.lower() == "mediatracker":
        return f"{owner}/MediaTracker"
    if repo.lower() == "mediatracker-hive":
        return f"{owner}/Mediatracker-hive"
    return f"{owner}/{repo}"


def current_sha() -> str | None:
    raw = (os.environ.get("MEDIATRACKER_GIT_SHA") or os.environ.get("GIT_SHA") or "").strip()
    if not raw or raw.lower() in {"dev", "unknown"}:
        return None
    return raw


def build_date() -> str | None:
    raw = (os.environ.get("MEDIATRACKER_BUILD_DATE") or "").strip()
    return raw or None


def stored_update(db: Session) -> UpdateStatus:
    available = _text(db, _KEY_AVAILABLE) == "true"
    checked = _text(db, _KEY_CHECKED)
    moment = None
    if checked:
        try:
            moment = datetime.fromisoformat(checked)
        except ValueError:
            moment = None
    return UpdateStatus(
        available=available,
        current_sha=current_sha(),
        remote_sha=_text(db, _KEY_REMOTE) or None,
        build_date=build_date(),
        detail=_text(db, _KEY_DETAIL),
        checked_at=moment,
    )


def check_for_update(db: Session, settings: Settings, *, transport: httpx.BaseTransport | None = None) -> UpdateStatus:
    """Prefer the GHCR image label. Fall back to the latest commit on main."""
    local = current_sha()
    ghcr = _secret(db, "ghcr_read_token", settings)
    github = _secret(db, "github_update_token", settings)
    if local is None:
        raise UpdateError("Cette instance n'indique pas le commit de l'image.")
    try:
        if ghcr:
            remote, digest = _ghcr_revision(ghcr, transport)
            source = "l'image publiée"
        else:
            try:
                remote, digest = _ghcr_revision(None, transport)
                source = "l'image publiée"
            except UpdateError:
                if not github:
                    raise UpdateError(
                        "Ajoutez un jeton GHCR en lecture, ou un jeton GitHub, pour comparer l'image."
                    ) from None
                remote, digest = _github_main(github, transport), None
                source = "le dernier commit de main"
    except UpdateError:
        raise
    except Exception as exc:
        _log_update_failure(exc)
        raise UpdateError(f"Comparaison impossible ({type(exc).__name__}).") from None
    available = not _same_sha(local, remote)
    detail = _detail(available, local, remote, digest, source)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _save(db, available, remote, detail, now)
    if available:
        record_event(
            db,
            "update.available",
            level="info",
            payload={
                "title": "MediaTracker",
                "remote_sha": remote,
                "current_sha": local,
                "level": "info",
            },
            dedupe_key=f"update.available:{remote}",
        )
    return UpdateStatus(
        available=available,
        current_sha=local,
        remote_sha=remote,
        build_date=build_date(),
        detail=detail,
        checked_at=now,
    )


def _ghcr_revision(token: str | None, transport: httpx.BaseTransport | None) -> tuple[str, str | None]:
    owner, repo = _parse_image_ref()
    basic = None
    if token:
        basic = b64encode(f"{owner}:{token}".encode()).decode("ascii")
    with httpx.Client(transport=transport, timeout=20.0, follow_redirects=True) as client:
        registry = _registry_token(client, owner, repo, basic)
        manifest, digest = _manifest(client, registry, owner, repo, "latest")
        revision = _revision_of(manifest)
        if revision:
            return revision, digest
        config_digest = _image_config_digest(client, registry, owner, repo, manifest)
        found = _labels(_blob(client, registry, owner, repo, config_digest)).get(_REVISION)
    if not isinstance(found, str) or not found.strip():
        raise UpdateError("L'image publiée n'indique pas son commit.")
    return found.strip(), digest


def _registry_token(client: httpx.Client, owner: str, repo: str, basic: str | None) -> str:
    headers: dict[str, str] = {}
    if basic:
        headers["Authorization"] = f"Basic {basic}"
    try:
        opened = client.get(
            "https://ghcr.io/token",
            params={"service": "ghcr.io", "scope": f"repository:{owner}/{repo}:pull"},
            headers=headers,
        )
    except httpx.HTTPError:
        raise UpdateError("Le registre d'images est injoignable.") from None
    if opened.status_code in {401, 403}:
        raise UpdateError("Le jeton GHCR a été refusé.")
    if opened.status_code >= 400:
        raise UpdateError("Le registre d'images a répondu une erreur.")
    payload = _read_json(opened, "La réponse du registre n'est pas un JSON lisible.")
    registry = _registry_bearer(payload)
    if not registry:
        raise UpdateError("Le registre d'images n'a pas fourni d'accès.")
    return registry


def _image_config_digest(client: httpx.Client, registry: str, owner: str, repo: str, manifest: dict) -> str:
    pointer = _config_digest(manifest)
    if pointer is None:
        raise UpdateError("L'image publiée n'indique pas son commit.")
    digest, kind = pointer
    if kind == "manifest":
        nested, _digest = _manifest(client, registry, owner, repo, digest)
        pointer = _config_digest(nested)
        if pointer is None or pointer[1] != "config":
            raise UpdateError("L'image publiée n'indique pas son commit.")
        digest = pointer[0]
    return digest


def _manifest(
    client: httpx.Client, registry_token: str, owner: str, repo: str, reference: str
) -> tuple[dict, str | None]:
    try:
        response = client.get(
            f"https://ghcr.io/v2/{owner}/{repo}/manifests/{reference}",
            headers={"Authorization": f"Bearer {registry_token}", "Accept": _ACCEPT},
        )
    except httpx.HTTPError:
        raise UpdateError("Le registre d'images est injoignable.") from None
    if response.status_code in {401, 403}:
        raise UpdateError("Le jeton GHCR a été refusé.")
    if response.status_code == 404:
        raise UpdateError("L'étiquette latest est introuvable.")
    if response.status_code >= 400:
        raise UpdateError("Le registre d'images a répondu une erreur.")
    payload = _read_json(response, "La réponse du registre n'est pas un JSON lisible.")
    if not isinstance(payload, dict):
        raise UpdateError("Manifeste d'image illisible.")
    raw = response.headers.get("docker-content-digest")
    return payload, raw.strip() if isinstance(raw, str) and raw.strip() else None


def _blob(client: httpx.Client, registry_token: str, owner: str, repo: str, digest: str) -> dict:
    try:
        response = client.get(
            f"https://ghcr.io/v2/{owner}/{repo}/blobs/{digest}",
            headers={"Authorization": f"Bearer {registry_token}"},
        )
    except httpx.HTTPError:
        raise UpdateError("Le registre d'images est injoignable.") from None
    if response.status_code >= 400:
        raise UpdateError("Le registre d'images a répondu une erreur.")
    payload = _read_json(response, "La réponse du registre n'est pas un JSON lisible.")
    if not isinstance(payload, dict):
        raise UpdateError("Configuration d'image illisible.")
    return payload


def _github_main(token: str, transport: httpx.BaseTransport | None) -> str:
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{_github_repo_for_image()}/commits/main",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "mediatracker",
            },
            transport=transport,
            timeout=20.0,
        )
    except httpx.HTTPError:
        raise UpdateError("GitHub est injoignable.") from None
    if response.status_code in {401, 403, 404}:
        raise UpdateError("Le jeton GitHub a été refusé.")
    if response.status_code >= 400:
        raise UpdateError("GitHub a répondu une erreur.")
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = _read_json(response, "La réponse de GitHub n'est pas un JSON lisible.")
        sha = payload.get("sha") if isinstance(payload, dict) else None
    else:
        sha = None
    if not isinstance(sha, str) or not sha.strip():
        raise UpdateError("GitHub n'a pas renvoyé le commit de main.")
    return sha.strip()


def _registry_bearer(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("token", "access_token"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _read_json(response: httpx.Response, message: str):
    """Parse a body. An unreadable payload becomes an UpdateError the UI can show."""
    raw = (response.text or "").strip()
    if not raw:
        raise UpdateError(message)
    lowered = raw[:64].lower()
    if lowered.startswith("<!doctype") or lowered.startswith("<html"):
        raise UpdateError(
            "Le registre a renvoyé une page HTML au lieu d'un manifeste. "
            "Vérifiez le jeton GHCR et les droits de lecture sur l'image."
        )
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _log_update_failure(exc)
        raise UpdateError(message) from None


def _log_update_failure(exc: BaseException) -> None:
    """Type and message, with credentials removed. The UI never receives the raw text."""
    logger.warning(
        "Vérification de mise à jour impossible (%s) : %s",
        type(exc).__name__,
        _USERINFO.sub(r"\1[redacted]@", redact(str(exc))),
    )


def _revision_of(manifest: dict) -> str | None:
    notes = manifest.get("annotations")
    if isinstance(notes, dict) and isinstance(notes.get(_REVISION), str) and notes[_REVISION].strip():
        return notes[_REVISION].strip()
    return None


def _config_digest(manifest: dict) -> tuple[str, str] | None:
    config = manifest.get("config")
    if isinstance(config, dict) and isinstance(config.get("digest"), str):
        return config["digest"], "config"
    manifests = manifest.get("manifests")
    if isinstance(manifests, list):
        preferred = (
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        )
        for media_type in preferred:
            for item in manifests:
                if not isinstance(item, dict) or not isinstance(item.get("digest"), str):
                    continue
                if item.get("mediaType") == media_type:
                    return item["digest"], "manifest"
        for item in manifests:
            if isinstance(item, dict) and isinstance(item.get("digest"), str):
                return item["digest"], "manifest"
    return None


def _labels(blob: dict) -> dict:
    config = blob.get("config")
    if isinstance(config, dict) and isinstance(config.get("Labels"), dict):
        return config["Labels"]
    return {}


def _same_sha(local: str, remote: str) -> bool:
    left, right = local.lower(), remote.lower()
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 7 and long.startswith(short)


def _detail(available: bool, local: str, remote: str, digest: str | None, source: str) -> str:
    if available:
        extra = f" ({digest})" if digest else ""
        return f"Mise à jour disponible : {source} est {remote[:12]}{extra}, cette image est {local[:12]}."
    return f"Cette image est à jour ({local[:12]})."


def _save(db: Session, available: bool, remote: str, detail: str, now: datetime) -> None:
    _put(db, _KEY_AVAILABLE, "true" if available else "false", now)
    _put(db, _KEY_REMOTE, remote, now)
    _put(db, _KEY_DETAIL, detail, now)
    _put(db, _KEY_CHECKED, now.isoformat(), now)


def _put(db: Session, key: str, value: str, now: datetime) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value, is_secret=False, updated_at=now))
        return
    row.value = value
    row.is_secret = False
    row.updated_at = now


def _text(db: Session, key: str) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        return ""
    return row.value


def _secret(db: Session, key: str, settings: Settings) -> str:
    row = db.get(Setting, key)
    if row is None or not row.value:
        fallback = getattr(settings, key, None)
        return fallback.strip() if isinstance(fallback, str) else ""
    plain = decrypt_secret(row.value, settings.secret_key)
    return plain.strip()

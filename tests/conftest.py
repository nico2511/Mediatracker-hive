import ipaddress
import os
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mediatracker.api.app import create_app
from mediatracker.config import Settings

# Taille logique réaliste (métadonnées catalogue / filtre débit) — pas la taille disque réelle.
PLAUSIBLE_VIDEO_BYTES = 800 * 1024 * 1024

# Octets réellement écrits avant truncate sparse (paramétrable : MEDIATRACKER_TEST_VIDEO_ON_DISK_PREFIX_BYTES).
_DEFAULT_ON_DISK_PREFIX = 4096


def on_disk_video_prefix_bytes() -> int:
    raw = os.environ.get("MEDIATRACKER_TEST_VIDEO_ON_DISK_PREFIX_BYTES")
    if raw is None or raw == "":
        return _DEFAULT_ON_DISK_PREFIX
    return max(1, int(raw))


def catalog_min_logical_video_bytes() -> int:
    """Taille minimale publication catalogue local (``hive.catalog_validity``)."""
    from mediatracker.hive.catalog_validity import MIN_PLAUSIBLE_VIDEO_BYTES

    return MIN_PLAUSIBLE_VIDEO_BYTES


def catalog_unknown_legacy_min_bytes() -> int:
    """Plancher version unknown+unknown pour publication locale (``hive.catalog_validity``)."""
    from mediatracker.hive.catalog_validity import UNKNOWN_LEGACY_SUSPECT_BYTES

    return UNKNOWN_LEGACY_SUSPECT_BYTES


def write_sparse_video_file(
    path: Path,
    logical_size: int,
    *,
    prefix: bytes | None = None,
) -> None:
    """Vidéo de test : ``logical_size`` pour stat/DB, empreinte disque minimale."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_prefix = min(on_disk_video_prefix_bytes(), max(0, logical_size))
    seed = (prefix or b"x")[:1] or b"\0"
    with path.open("wb") as fh:
        if n_prefix > 0:
            fh.write(seed * n_prefix)
    if logical_size > 0:
        os.truncate(path, logical_size)


def sparse_payload_alloc_bytes(path: Path) -> int:
    """Blocs disque alloués (POSIX ``st_blocks`` × 512)."""
    st = path.stat()
    return int(getattr(st, "st_blocks", 0)) * 512

# Clé factice : débloque les sources gated AllDebrid sans réseau (voir #232).
# < 20 caractères : le scan-secrets refuse les littéraux `*_api_key="..."` trop longs.
FICTIVE_ALLDEBRID_API_KEY = "fictive-adb-tst-k"


def connect_alldebrid_for_tests(app) -> None:
    """Provider AllDebrid activé + clé « connectée » pour le pipeline."""
    app.state.settings = app.state.settings.model_copy(update={"alldebrid_api_key": FICTIVE_ALLDEBRID_API_KEY})
    from mediatracker.db.models import Provider

    db = app.state.session_factory()
    try:
        row = db.query(Provider).filter(Provider.type == "alldebrid").one()
        row.enabled = True
        db.commit()
    finally:
        db.close()

_REAL_GETADDRINFO = socket.getaddrinfo


def _loopback_host(host: str) -> bool:
    name = (host or "").lower().rstrip(".")
    if name in {"localhost", "::1"} or name.startswith("127."):
        return True
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def ssrf_dns_stub(monkeypatch):
    """No real DNS for fictional hosts; loopback keeps real resolution (aria2 live, healthchecks)."""

    def _addrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if _loopback_host(host):
            return _REAL_GETADDRINFO(host, port, family, type, proto, flags)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.99", 0))]

    monkeypatch.setattr("mediatracker.security.ssrf.socket.getaddrinfo", _addrinfo)


@pytest.fixture
def ssrf_real_dns(monkeypatch):
    """Restore system DNS (integration tests that need non-loopback resolution)."""
    monkeypatch.setattr("mediatracker.security.ssrf.socket.getaddrinfo", _REAL_GETADDRINFO)


@pytest.fixture(autouse=True)
def reset_classify_throttle():
    from mediatracker.core.cleanup_classify import reset_classify_schedule

    reset_classify_schedule()
    yield
    reset_classify_schedule()


@pytest.fixture(autouse=True)
def secret_key_env(monkeypatch):
    """Tests must not depend on a SECRET_KEY exported in the runner environment."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789")


@pytest.fixture(autouse=True)
def no_real_tmdb_in_tests(monkeypatch):
    """Les tests ne doivent jamais appeler TMDB réel, même si TMDB_READ_TOKEN est défini."""
    monkeypatch.delenv("TMDB_READ_TOKEN", raising=False)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLDEBRID_API_KEY", raising=False)
    return Settings(
        secret_key="test-secret-key-0123456789",
        admin_username="admin",
        admin_password="test-admin-password",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        data_dir=tmp_path / "data",
        config_dir=tmp_path / "config",
        disk_min_free_gb=0,
        alldebrid_api_key=None,
    )


@pytest.fixture
def app(settings):
    application = create_app(settings)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def wizard_client(tmp_path):
    application = create_app(
        Settings(
            secret_key="wizard-secret-key-0123456789",
            admin_password=None,
            database_url=f"sqlite:///{tmp_path / 'app.db'}",
            data_dir=tmp_path / "data",
            config_dir=tmp_path / "config",
            disk_min_free_gb=0,
        )
    )
    with TestClient(application) as test_client:
        yield application, test_client
    application.state.engine.dispose()


def login(client, username="admin", password="test-admin-password"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


# Profil par défaut (VF/Multi 1080p strict) : langue + résolution explicites sur les liens de test.
PROFILE_COMPLIANT_QUALITY = "1080p"
PROFILE_COMPLIANT_LANGUAGE = "MULTI"
FICTIVE_RELEASE_STEM = "Exemple.Fictif.2099.MULTi.1080p.WEB"

PROFILE_LINK_JSON_FIELDS: dict[str, str] = {
    "quality": PROFILE_COMPLIANT_QUALITY,
    "language": PROFILE_COMPLIANT_LANGUAGE,
}


def profile_compliant_release_filename(
    *,
    season: int | None = None,
    episode: int | None = None,
    ext: str = ".mkv",
) -> str:
    stem = FICTIVE_RELEASE_STEM
    if season is not None and episode is not None:
        stem += f".S{season:02d}E{episode:02d}"
    return stem + ext


def profile_compliant_db_link_fields(
    *,
    season: int | None = None,
    episode: int | None = None,
) -> dict[str, str]:
    """Champs ``Link`` SQLAlchemy alignés sur le profil par défaut."""
    return {
        "quality": PROFILE_COMPLIANT_QUALITY,
        "language": PROFILE_COMPLIANT_LANGUAGE,
        "filename": profile_compliant_release_filename(season=season, episode=episode),
    }


def link_create_json(
    media_id: int,
    url: str,
    *,
    season: int | None = 1,
    episode: int | None = 1,
    start: bool = True,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "media_id": media_id,
        "url": url,
        "start": start,
        **PROFILE_LINK_JSON_FIELDS,
    }
    if season is not None:
        payload["season"] = season
    if episode is not None:
        payload["episode"] = episode
    payload.update(extra)
    return payload


def post_test_link(
    client,
    media_id: int,
    url: str,
    *,
    season: int | None = 1,
    episode: int | None = 1,
    start: bool = True,
    **extra: object,
) -> dict:
    response = client.post(
        "/api/v1/links",
        json=link_create_json(
            media_id,
            url,
            season=season,
            episode=episode,
            start=start,
            **extra,
        ),
        headers=csrf(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def csrf(client) -> dict[str, str]:
    token = client.cookies.get("mt_csrf")
    assert token
    return {"X-CSRF-Token": token}

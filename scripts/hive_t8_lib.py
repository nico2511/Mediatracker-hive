"""Bibliothèque partagée pour la recette Hive T8 (proxy réel, sans ASGI de test)."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SANDBOX_DIR = REPO_ROOT / "sandbox" / "hive-t8"
RUNTIME_DIR = SANDBOX_DIR / "runtime"
CERTS_DIR = SANDBOX_DIR / "certs"
PIDS_FILE = RUNTIME_DIR / "pids.json"
HOSTS_DELTA = RUNTIME_DIR / "hosts.delta"

SANDBOX_IP = "203.0.113.99"
PROXY_TLS_PORT = 19443
HOSTNAMES = ("mtr.exemple.fr", "mtr-test.exemple.fr", "mtr-third.exemple.fr")
URL_Y = f"https://{HOSTNAMES[0]}:{PROXY_TLS_PORT}"
URL_X = f"https://{HOSTNAMES[1]}:{PROXY_TLS_PORT}"
URL_Z = f"https://{HOSTNAMES[2]}:{PROXY_TLS_PORT}"

PORTS = {"y": 19001, "x": 19002, "z": 19003}
ADMIN_PASSWORD = "sandbox-admin-password"
NOW = datetime(2026, 11, 7, 14, 0, tzinfo=timezone.utc)
REACHABILITY_TTL_DOC = "REACHABILITY_TTL_SECONDS=3600 (peer_gossip)"


def repo_python() -> str:
    return sys.executable


def _read_pids() -> list[dict[str, Any]]:
    if not PIDS_FILE.exists():
        return []
    try:
        data = json.loads(PIDS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _write_pids(rows: list[dict[str, Any]]) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PIDS_FILE.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def track_pid(*, name: str, pid: int, kind: str) -> None:
    rows = _read_pids()
    rows.append({"name": name, "pid": pid, "kind": kind, "registered_at": NOW.isoformat()})
    _write_pids(rows)


def untrack_pid(pid: int) -> None:
    rows = [r for r in _read_pids() if r.get("pid") != pid]
    _write_pids(rows)


def cleanup_tracked_processes() -> list[str]:
    """Arrête uniquement les PID enregistrés par cette recette."""
    notes: list[str] = []
    for row in _read_pids():
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            notes.append(f"SIGTERM pid {pid} ({row.get('name')})")
        except ProcessLookupError:
            notes.append(f"pid {pid} déjà terminé")
        except OSError as exc:
            notes.append(f"pid {pid}: {exc}")
    time.sleep(0.3)
    for row in _read_pids():
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            continue
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
            notes.append(f"SIGKILL pid {pid}")
        except (ProcessLookupError, OSError):
            pass
    _write_pids([])
    return notes


def ensure_hosts_file() -> list[str]:
    """Assure la ligne sandbox (IP DOCUMENTATION) ; retire une entrée obsolète (ex. ancienne IP privée)."""
    notes: list[str] = []
    line = f"{SANDBOX_IP} {' '.join(HOSTNAMES)}"
    hosts_path = Path("/etc/hosts")
    try:
        hosts = hosts_path.read_text(encoding="utf-8") if hosts_path.is_file() else ""
        hostnames = set(HOSTNAMES)
        lines = hosts.splitlines()
        stale = [
            ln
            for ln in lines
            if hostnames.intersection(ln.split()) and not ln.strip().startswith(SANDBOX_IP)
        ]
        if stale:
            notes.append(f"Entrée hosts obsolète détectée : {stale[0].strip()}")
            lines = [ln for ln in lines if ln not in stale]
            hosts_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        if line in hosts or any(ln.strip() == line for ln in lines):
            notes.append("Entrées /etc/hosts déjà présentes.")
            return notes
        subprocess.run(
            ["sudo", "sh", "-c", f'echo "{line}" >> /etc/hosts'],
            check=True,
            capture_output=True,
            text=True,
        )
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        HOSTS_DELTA.write_text(line + "\n", encoding="utf-8")
        notes.append(f"Ajouté à /etc/hosts (delta enregistré) : {line}")
    except (OSError, subprocess.CalledProcessError) as exc:
        notes.append(f"/etc/hosts non modifié ({exc}). Ajouter manuellement : {line}")
    return notes


def revert_hosts_delta() -> list[str]:
    """Retire uniquement la ligne ajoutée par cette exécution (si delta présent)."""
    notes: list[str] = []
    if not HOSTS_DELTA.is_file():
        return notes
    line = HOSTS_DELTA.read_text(encoding="utf-8").strip()
    if not line:
        return notes
    hosts_path = Path("/etc/hosts")
    try:
        if not hosts_path.is_file():
            notes.append("/etc/hosts absent, rien à retirer.")
            return notes
        text = hosts_path.read_text(encoding="utf-8")
        new_lines = [ln for ln in text.splitlines() if ln.strip() != line]
        if len(new_lines) == len(text.splitlines()):
            notes.append("Ligne hosts delta introuvable (conservée telle quelle).")
            return notes
        hosts_path.write_text("\n".join(new_lines) + ("\n" if new_lines else ""), encoding="utf-8")
        HOSTS_DELTA.unlink(missing_ok=True)
        notes.append(f"Retiré de /etc/hosts (delta seul) : {line}")
    except OSError as exc:
        notes.append(f"Impossible de retirer le delta hosts : {exc}")
    return notes


def ensure_sandbox_ip() -> list[str]:
    """Ajoute 203.0.113.99/32 sur lo si absent (nécessite sudo)."""
    notes: list[str] = []
    try:
        out = subprocess.run(
            ["ip", "-4", "addr", "show", "dev", "lo"],
            check=True,
            capture_output=True,
            text=True,
        )
        if SANDBOX_IP in out.stdout:
            notes.append(f"IP {SANDBOX_IP} déjà présente sur lo.")
            return notes
        subprocess.run(
            ["sudo", "ip", "addr", "add", f"{SANDBOX_IP}/32", "dev", "lo"],
            check=True,
            capture_output=True,
            text=True,
        )
        notes.append(f"IP {SANDBOX_IP} ajoutée sur lo.")
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        notes.append(f"Impossible d'ajouter {SANDBOX_IP} sur lo : {exc}")
    return notes


def ensure_hosts_hint() -> str:
    return (
        f"# Entrée attendue (sudo) :\n"
        f"{SANDBOX_IP} {' '.join(HOSTNAMES)}"
    )


def generate_tls_material() -> Path:
    CERTS_DIR.mkdir(parents=True, exist_ok=True)
    ca_key = CERTS_DIR / "ca.key"
    ca_crt = CERTS_DIR / "ca.crt"
    srv_key = CERTS_DIR / "server.key"
    srv_crt = CERTS_DIR / "server.crt"
    if ca_crt.exists() and srv_crt.exists():
        return ca_crt
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_crt),
            "-days",
            "30",
            "-nodes",
            "-subj",
            "/CN=HiveSandboxCA",
        ],
        check=True,
        capture_output=True,
    )
    csr = CERTS_DIR / "server.csr"
    ext = CERTS_DIR / "server.ext"
    ext.write_text(
        "\n".join(
            [
                "subjectAltName = @alt_names",
                "[alt_names]",
                *(f"DNS.{i + 1} = {h}" for i, h in enumerate(HOSTNAMES)),
                f"IP.1 = {SANDBOX_IP}",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "openssl",
            "req",
            "-newkey",
            "rsa:2048",
            "-keyout",
            str(srv_key),
            "-out",
            str(csr),
            "-nodes",
            "-subj",
            "/CN=mtr.exemple.fr",
        ],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(srv_crt),
            "-days",
            "30",
            "-extfile",
            str(ext),
        ],
        check=True,
        capture_output=True,
    )
    return ca_crt


def write_nginx_config() -> Path:
    SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
    cfg = SANDBOX_DIR / "nginx.generated.conf"
    upstreams = "\n".join(
        f"    upstream mtr_{name} {{ server 127.0.0.1:{port}; }}"
        for name, port in PORTS.items()
    )
    servers = []
    mapping = (("y", HOSTNAMES[0]), ("x", HOSTNAMES[1]), ("z", HOSTNAMES[2]))
    for key, host in mapping:
        servers.append(
            f"""
    server {{
        listen {SANDBOX_IP}:{PROXY_TLS_PORT} ssl;
        server_name {host};
        ssl_certificate     /etc/nginx/certs/server.crt;
        ssl_certificate_key /etc/nginx/certs/server.key;
        location /hive/ {{
            proxy_pass http://mtr_{key};
            proxy_http_version 1.1;
            proxy_buffering off;
            proxy_request_buffering off;
            proxy_set_header Host $host;
            proxy_set_header Range $http_range;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }}
        location / {{
            return 401;
        }}
    }}"""
        )
    prefix_placeholder = "__NGINX_PREFIX__"
    cfg.write_text(
        f"""
worker_processes 1;
pid {prefix_placeholder}/logs/nginx.pid;
error_log {prefix_placeholder}/logs/error.log;
events {{ worker_connections 256; }}
http {{
    access_log {prefix_placeholder}/logs/access.log;
    client_body_temp_path {prefix_placeholder}/client_body_temp;
    proxy_temp_path {prefix_placeholder}/proxy_temp;
    fastcgi_temp_path {prefix_placeholder}/fastcgi_temp;
    uwsgi_temp_path {prefix_placeholder}/uwsgi_temp;
    scgi_temp_path {prefix_placeholder}/scgi_temp;
{upstreams}
{''.join(servers)}
}}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return cfg


@dataclass
class InstanceProc:
    name: str
    port: int
    public_url: str
    root: Path
    proc: subprocess.Popen[Any] | None = None
    log_path: Path | None = None

    peer_rate_limit: str | None = None

    def env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "SECRET_KEY": f"sandbox-secret-{self.name}-0123456789",
                "ADMIN_USERNAME": "admin",
                "ADMIN_PASSWORD": ADMIN_PASSWORD,
                "DATABASE_URL": f"sqlite:///{self.root / 'app.db'}",
                "DATA_DIR": str(self.root / "data"),
                "CONFIG_DIR": str(self.root / "config"),
                "DISK_MIN_FREE_GB": "0",
                "FORWARDED_ALLOW_IPS": "127.0.0.1",
                "PORT": str(self.port),
                "SSL_CERT_FILE": str(CERTS_DIR / "ca.crt"),
            }
        )
        if self.peer_rate_limit:
            env["HIVE_SANDBOX_PEER_RATE_LIMIT"] = self.peer_rate_limit
        return env

    def local_api_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.log_path = self.root / "uvicorn.log"
        logf = self.log_path.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                repo_python(),
                "-m",
                "uvicorn",
                "mediatracker.api.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "warning",
                "--proxy-headers",
                "--forwarded-allow-ips",
                "127.0.0.1",
            ],
            cwd=str(REPO_ROOT),
            env=self.env(),
            stdout=logf,
            stderr=subprocess.STDOUT,
        )
        assert self.proc.pid
        track_pid(name=self.name, pid=self.proc.pid, kind="uvicorn")
        _wait_port(self.port, timeout=30.0)

    def stop(self) -> None:
        if self.proc is None:
            return
        pid = self.proc.pid
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if pid:
            untrack_pid(pid)
        self.proc = None


def _wait_port(port: int, *, host: str = "127.0.0.1", timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            if s.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"port {host}:{port} non prêt")


@dataclass
class NginxProc:
    cfg: Path
    pid_file: Path = field(default_factory=lambda: RUNTIME_DIR / "nginx.pid")
    log_path: Path = field(default_factory=lambda: RUNTIME_DIR / "nginx.error.log")
    proc: subprocess.Popen[Any] | None = None

    def start(self) -> None:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        cert_src = CERTS_DIR
        runtime_certs = RUNTIME_DIR / "certs"
        if runtime_certs.exists():
            shutil.rmtree(runtime_certs)
        shutil.copytree(cert_src, runtime_certs)
        prefix = RUNTIME_DIR / "nginx-prefix"
        cfg_text = self.cfg.read_text(encoding="utf-8").replace(
            "/etc/nginx/certs/", str(runtime_certs) + "/"
        )
        cfg_text = cfg_text.replace("__NGINX_PREFIX__", str(prefix))
        runtime_cfg = RUNTIME_DIR / "nginx.runtime.conf"
        runtime_cfg.write_text(cfg_text, encoding="utf-8")
        for sub in ("logs", "tmp", "client_body_temp", "proxy_temp", "fastcgi_temp", "uwsgi_temp", "scgi_temp"):
            (prefix / sub).mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        err = self.log_path.open("a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                "nginx",
                "-p",
                str(prefix),
                "-c",
                str(runtime_cfg),
                "-g",
                "daemon off;",
            ],
            stdout=err,
            stderr=subprocess.STDOUT,
        )
        assert self.proc.pid
        track_pid(name="nginx", pid=self.proc.pid, kind="nginx")
        if self.proc.poll() is not None:
            raise RuntimeError(f"nginx a quitté : {self.log_path.read_text(encoding='utf-8')[-2000:]}")
        _wait_port(PROXY_TLS_PORT, host=SANDBOX_IP, timeout=30.0)

    def stop(self) -> None:
        if self.proc is None:
            return
        pid = self.proc.pid
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        if pid:
            untrack_pid(pid)
        self.proc = None


class ApiSession:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.client = httpx.Client(base_url=self.base, timeout=30.0, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def login(self) -> None:
        r = self.client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": ADMIN_PASSWORD},
        )
        if r.status_code != 200:
            raise RuntimeError(f"login failed {r.status_code}: {r.text}")

    def csrf(self) -> dict[str, str]:
        token = self.client.cookies.get("mt_csrf")
        if not token:
            raise RuntimeError("csrf cookie missing")
        return {"X-CSRF-Token": token}

    def enable_hive(self, name: str, public: str) -> None:
        self.login()
        r = self.client.patch(
            "/api/v1/settings",
            json={"values": {"hive_enabled": True, "hive_activation_name": name, "hive_public_address": public}},
            headers=self.csrf(),
        )
        if r.status_code != 200:
            raise RuntimeError(f"enable hive: {r.status_code} {r.text}")

    def identity(self) -> dict:
        self.login()
        r = self.client.get("/api/v1/hive/identity")
        r.raise_for_status()
        return r.json()

    def create_ruche(self) -> str:
        self.login()
        r = self.client.post("/api/v1/hive/ruches", headers=self.csrf())
        r.raise_for_status()
        return r.json()["ruche_id_hex"]

    def create_invitation(self, ruche_id_hex: str) -> str:
        self.login()
        r = self.client.post(
            "/api/v1/hive/ruches/invitations",
            json={"ruche_id_hex": ruche_id_hex},
            headers=self.csrf(),
        )
        r.raise_for_status()
        return r.json()["link"]

    def join_begin(self, link: str, name: str, public: str) -> dict:
        self.login()
        r = self.client.post(
            "/api/v1/hive/join/begin",
            json={"link": link, "invitee_name": name, "invitee_public_address": public},
            headers=self.csrf(),
        )
        r.raise_for_status()
        return r.json()

    def join_accept(self, pending_id: int) -> None:
        self.login()
        r = self.client.post(
            "/api/v1/hive/join/accept",
            json={"pending_id": pending_id},
            headers=self.csrf(),
        )
        if r.status_code != 204:
            raise RuntimeError(f"join accept: {r.status_code}")

    def join_confirm(self, ruche_id_hex: str, creator_url: str, creator_id: str) -> None:
        self.login()
        r = self.client.post(
            "/api/v1/hive/join/confirm",
            json={
                "ruche_id_hex": ruche_id_hex,
                "creator_base_url": creator_url,
                "creator_instance_id": creator_id,
            },
            headers=self.csrf(),
        )
        r.raise_for_status()

    def exclude(self, ruche_id_hex: str, instance_id: str) -> None:
        self.login()
        r = self.client.post(
            f"/api/v1/hive/ruches/{ruche_id_hex}/exclude",
            json={"instance_id": instance_id},
            headers=self.csrf(),
        )
        if r.status_code != 204:
            raise RuntimeError(f"exclude: {r.status_code} {r.text}")

    def get_catalogue(self, *, refresh: bool = False) -> dict:
        self.login()
        r = self.client.get("/api/v1/hive/catalogue", params={"refresh": "true"} if refresh else None)
        r.raise_for_status()
        return r.json()


def join_pair(
    *,
    creator: ApiSession,
    invitee: ApiSession,
    creator_public_url: str,
    invitee_public_url: str,
    invitee_name: str,
    ruche_id_hex: str | None = None,
) -> str:
    rid = ruche_id_hex or creator.create_ruche()
    creator_id = creator.identity()["instance_id"]
    link = creator.create_invitation(rid)
    begun = invitee.join_begin(link, invitee_name, invitee_public_url)
    creator.join_accept(int(begun["pending_id"]))
    invitee.join_confirm(rid, creator_public_url, creator_id)
    return rid


def seed_file_production(
    app,
    *,
    ruche_id: bytes,
    payload: bytes,
    title: str = "Sandbox",
    tmdb_id: int = 99901,
) -> tuple[str, str, str]:
    """Écrit le fichier sur disque, empreinte via worker, sync catalogue (sans publish_available direct)."""
    from sqlalchemy import select

    from mediatracker.core.storage import storage_layout
    from mediatracker.db.models import HiveEntry, HiveFileFingerprint, Media, MediaItem
    from mediatracker.hive.catalog import sync_hive_catalog
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.hive.fingerprint import ensure_fingerprint_row_for_item, hive_fingerprint_tick
    from mediatracker.hive.identity import hive_public_id_from_public_key, load_private_key
    from mediatracker.library.scanner import remember_filed

    settings = app.state.settings
    pk = load_private_key(settings)
    assert pk
    origin = hive_public_id_from_public_key(pk.public_key())
    db = app.state.session_factory()
    try:
        layout = storage_layout(db, settings)
        folder = layout.movies_dir / f"{title} (2099) [tmdbid-{tmdb_id}]"
        folder.mkdir(parents=True, exist_ok=True)
        video = folder / "sandbox.bin"
        video.write_bytes(payload)
        media = Media(type="movie", title=title, year=2099, tmdb_id=tmdb_id)
        db.add(media)
        db.flush()
        item = MediaItem(media_id=media.id, state="downloaded", downloaded=True, file_path=str(video.resolve()))
        db.add(item)
        db.flush()
        remember_filed(db, video.resolve(), media, item, NOW)
        db.commit()

        for _ in range(512):
            ensure_fingerprint_row_for_item(db, settings, item, now=NOW)
            db.commit()
            hive_fingerprint_tick(db, settings, now=NOW)
            db.commit()
            fp = db.get(HiveFileFingerprint, item.id)
            if fp and fp.sha256_hex:
                break
        else:
            raise RuntimeError("hive_fingerprint_tick n'a pas produit de sha256")

        fp = db.get(HiveFileFingerprint, item.id)
        assert fp and fp.sha256_hex
        assert fp.sha256_hex == file_sha256_hex(video)

        sync_hive_catalog(db, settings, now=NOW)
        db.commit()
        entry = db.scalar(
            select(HiveEntry).where(HiveEntry.ruche_id == ruche_id, HiveEntry.origin_instance_id == origin).limit(1)
        )
        assert entry and entry.file_id and entry.etat == "disponible"
        return origin, entry.file_id.hex(), fp.sha256_hex
    finally:
        db.close()


def pull_gossip(
    db,
    settings,
    *,
    ruche_id: bytes,
    peer_url: str,
    force: bool = True,
) -> None:
    from mediatracker.hive.gossip_worker import PeerExchangeTarget, hive_gossip_tick

    hive_gossip_tick(
        db,
        settings,
        now=NOW,
        force=force,
        peer_targets=[PeerExchangeTarget(ruche_id=ruche_id, peer_base_url=peer_url)],
    )


def sync_catalog(db, settings) -> None:
    from mediatracker.hive.catalog import sync_hive_catalog

    sync_hive_catalog(db, settings, now=NOW)


@dataclass
class ProxyReceiveResult:
    """Résultat d'une réception pair HTTP (recette T8, hors HiveTransferJob)."""

    state: str = "pending"
    sha256_hex: str = ""
    bytes_done: int = 0
    part_path: str = ""
    paused_reason: str | None = None


_RECEIVE_CHUNK = 1024 * 1024
_DEFAULT_FICHE = {"type": "movie", "tmdb_id": 99902, "title": "SandboxRelay", "year": 2099}
_DEFAULT_CLE = {"type": "movie", "tmdb_id": 99902, "version": "u+u"}


def _ensure_media_item(db, fiche: dict, cle: dict) -> tuple[Any, Any]:
    from sqlalchemy import select

    from mediatracker.db.models import Media, MediaItem

    tmdb_id = int(fiche.get("tmdb_id") or cle.get("tmdb_id") or 0)
    raw_type = fiche.get("type") or cle.get("type")
    media_type = "film" if raw_type in ("movie", "film") else "series"
    media = db.scalar(select(Media).where(Media.tmdb_id == tmdb_id, Media.type == media_type).limit(1))
    if media is None:
        media = Media(
            type=media_type,
            title=str(fiche.get("title") or "Sandbox"),
            year=int(fiche.get("year") or 2099),
            tmdb_id=tmdb_id,
            followed=True,
        )
        db.add(media)
        db.flush()
    item = db.scalar(select(MediaItem).where(MediaItem.media_id == media.id).limit(1))
    if item is None:
        item = MediaItem(media_id=media.id, state="missing")
        db.add(item)
        db.flush()
    return media, item


def download_peer_file(
    db,
    settings,
    *,
    sources: list[dict],
    sha256_hex: str,
    expected_size: int,
    part_path: Path,
    fiche: dict | None = None,
    cle: dict | None = None,
    max_bytes: int | None = None,
    finalize: bool = False,
) -> ProxyReceiveResult:
    """Télécharge un fichier via GET /hive/files signé (reprise sur .part existant)."""
    from mediatracker.hive.chunk_digest import file_sha256_hex
    from mediatracker.hive.hive_received_filing import finalize_received_hive_file
    from mediatracker.hive.identity import load_private_key
    from mediatracker.hive.peer_auth import sign_request_headers
    from mediatracker.hive.peer_gossip import mark_origin_unreachable
    from mediatracker.hive.peer_member import member_base_url

    fiche = dict(fiche or _DEFAULT_FICHE)
    cle = dict(cle or _DEFAULT_CLE)
    result = ProxyReceiveResult(sha256_hex=str(sha256_hex).lower(), part_path=str(part_path))
    if not sources:
        result.state = "failed"
        result.paused_reason = "source_down"
        return result

    src = sources[0]
    try:
        ruche_id = bytes.fromhex(str(src["ruche_id_hex"]))
        file_id_hex = str(src["file_id_hex"])
        origin = str(src["origine"])
    except (KeyError, TypeError, ValueError):
        result.state = "failed"
        result.paused_reason = "source_down"
        return result

    base = member_base_url(db, ruche_id, origin)
    if not base:
        result.state = "paused"
        result.paused_reason = "source_down"
        return result

    pk = load_private_key(settings)
    if pk is None:
        result.state = "failed"
        return result

    http_path = f"/hive/files/{file_id_hex}"
    part_path.parent.mkdir(parents=True, exist_ok=True)
    offset = part_path.stat().st_size if part_path.is_file() else 0
    goal = min(expected_size, max_bytes) if max_bytes is not None else expected_size

    ca = os.environ.get("SSL_CERT_FILE")
    verify: bool | str = ca if ca else True

    try:
        while offset < goal:
            end = min(offset + _RECEIVE_CHUNK - 1, expected_size - 1)
            headers = sign_request_headers(
                private_key=pk,
                method="GET",
                path=http_path,
                query="",
                ruche_id=ruche_id,
                body=b"",
            )
            headers["Range"] = f"bytes={offset}-{end}"
            with httpx.Client(verify=verify, timeout=120.0) as client:
                resp = client.get(base.rstrip("/") + http_path, headers=headers)
            if resp.status_code not in (200, 206):
                mark_origin_unreachable(db, ruche_id=ruche_id, origin_id=origin, now=NOW)
                db.flush()
                result.bytes_done = offset
                result.state = "paused"
                result.paused_reason = "source_down"
                return result
            if not resp.content:
                break
            with part_path.open("ab" if offset else "wb") as handle:
                handle.write(resp.content)
            offset += len(resp.content)
        result.bytes_done = offset
    except httpx.HTTPError:
        mark_origin_unreachable(db, ruche_id=ruche_id, origin_id=origin, now=NOW)
        db.flush()
        result.bytes_done = offset
        result.state = "paused"
        result.paused_reason = "source_down"
        return result

    if offset < expected_size:
        result.state = "partial"
        return result

    got = file_sha256_hex(part_path)
    if got != result.sha256_hex:
        result.state = "failed"
        return result
    result.state = "verified"

    if finalize:
        media, item = _ensure_media_item(db, fiche, cle)
        err = finalize_received_hive_file(
            db,
            settings,
            part=part_path,
            media=media,
            item=item,
            fiche=fiche,
            cle=cle,
            sha256_hex=result.sha256_hex,
            now=NOW,
        )
        if err:
            result.state = "failed"
            result.paused_reason = err
        else:
            result.state = "done"
    return result


def transfer_via_proxy(
    db,
    settings,
    *,
    sha: str,
    size: int,
    sources: list[dict],
    finalize: bool = True,
) -> ProxyReceiveResult:
    """Réception pair derrière l'URL publique (proxy TLS), chemin fetch-grant + rangement."""
    part = Path(settings.data_dir) / "sandbox" / "hive-t8-receive.part"
    return download_peer_file(
        db,
        settings,
        sources=sources,
        sha256_hex=sha,
        expected_size=size,
        part_path=part,
        finalize=finalize,
    )


def portal_blocks_non_hive(public_url: str) -> tuple[bool, str]:
    ca = str(CERTS_DIR / "ca.crt")
    with httpx.Client(verify=ca, timeout=10.0) as client:
        r = client.get(public_url.rstrip("/") + "/api/v1/health")
        if r.status_code == 401:
            return True, "401 sur / (portail simulé)"
        return False, f"statut inattendu {r.status_code}"


def save_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def assert_no_leak(payload_json: str | None, *, forbidden: set[str]) -> list[str]:
    blob = payload_json or ""
    return [tok for tok in forbidden if tok and tok in blob]

"""Helpers to exercise a real aria2c binary with the same options as the container image."""

from __future__ import annotations

import re
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from mediatracker.providers.downloaders.aria2 import Aria2Client

_RUN_SCRIPT = Path(__file__).resolve().parents[1] / "docker" / "s6-rc.d" / "aria2" / "run"
_CONF_BLOCK = re.compile(r"cat > /config/aria2\.conf <<EOF\n(.*?)^EOF", re.MULTILINE | re.DOTALL)


def aria2c_path() -> str | None:
    return shutil.which("aria2c")


def require_aria2c() -> str:
    path = aria2c_path()
    if not path:
        pytest.skip("aria2c absent de l'environnement local")
    return path


def free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def aria2_option_lines_from_run_script() -> list[str]:
    """Read key=value lines from ``docker/s6-rc.d/aria2/run`` (no duplicated option list)."""
    text = _RUN_SCRIPT.read_text(encoding="utf-8")
    match = _CONF_BLOCK.search(text)
    if not match:
        raise RuntimeError("Bloc aria2.conf introuvable dans le script run.")
    lines: list[str] = []
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def write_aria2_conf(
    path: Path,
    *,
    rpc_port: int,
    rpc_secret: str,
    incomplete_dir: Path,
    session_path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    incomplete_dir.mkdir(parents=True, exist_ok=True)
    overrides = {
        "rpc-listen-port": str(rpc_port),
        "rpc-secret": rpc_secret,
        "dir": str(incomplete_dir),
        "save-session": str(session_path),
        "input-file": str(session_path),
    }
    body_lines: list[str] = []
    for line in aria2_option_lines_from_run_script():
        key = line.split("=", 1)[0]
        if key in overrides:
            body_lines.append(f"{key}={overrides[key]}")
        elif "${" in line:
            raise RuntimeError(f"Ligne aria2 non résolue dans le script run : {line}")
        else:
            body_lines.append(line)
    path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    if not session_path.exists():
        session_path.touch()


class LiveAria2:
    def __init__(self, root: Path, *, rpc_secret: str = "integration-test-secret") -> None:
        self.root = root
        self.rpc_secret = rpc_secret
        self.config_dir = root / "config"
        self.incomplete_dir = root / "downloads" / "incomplete"
        self.session_path = self.config_dir / "aria2.session"
        self.conf_path = self.config_dir / "aria2.conf"
        self.rpc_port = free_tcp_port()
        self.rpc_url = f"http://127.0.0.1:{self.rpc_port}/jsonrpc"
        self._proc: subprocess.Popen[bytes] | None = None
        self.client: Aria2Client | None = None

    def start(self) -> Aria2Client:
        require_aria2c()
        write_aria2_conf(
            self.conf_path,
            rpc_port=self.rpc_port,
            rpc_secret=self.rpc_secret,
            incomplete_dir=self.incomplete_dir,
            session_path=self.session_path,
        )
        self._proc = subprocess.Popen(
            ["aria2c", f"--conf-path={self.conf_path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        client = Aria2Client(self.rpc_url, self.rpc_secret)
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError("aria2c s'est arrêté pendant le démarrage.")
            try:
                client.get_version()
                self.client = client
                return client
            except Exception:
                time.sleep(0.1)
        client.close()
        raise RuntimeError("aria2c RPC injoignable après démarrage.")

    def stop(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


@contextmanager
def live_aria2(tmp_path: Path):
    daemon = LiveAria2(tmp_path)
    client = daemon.start()
    try:
        yield daemon, client
    finally:
        daemon.stop()


def wait_until(client: Aria2Client, gid: str, *states: str, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    wanted = set(states)
    while time.monotonic() < deadline:
        status = client.tell_status(gid)
        if status.state in wanted:
            return status
        time.sleep(0.15)
    last = client.tell_status(gid)
    raise AssertionError(f"aria2 gid={gid} état={last.state}, attendu {wanted}")


def tell_queue(client: Aria2Client, method: str) -> list:
    if method == "aria2.tellActive":
        # tellActive accepts optional ``keys`` only (no offset/num unlike waiting/stopped).
        params: list = []
    else:
        params = [0, 100]
    result = client._call(method, params)
    return result if isinstance(result, list) else []


def list_incomplete_files(incomplete_dir: Path) -> set[str]:
    if not incomplete_dir.is_dir():
        return set()
    return {path.name for path in incomplete_dir.iterdir() if path.is_file()}

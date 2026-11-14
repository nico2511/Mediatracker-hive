"""Vérifie le script CI de détection de secrets et d'infos personnelles."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCAN_SCRIPT = _REPO_ROOT / ".github" / "scripts" / "scan-secrets.py"
_EXAMPLE_BLOCKED = "perso.invalid"


def _load_scan_module():
    spec = importlib.util.spec_from_file_location("scan_secrets", _SCAN_SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_private_ip_detection():
    scan = _load_scan_module()
    assert scan.is_private_ipv4("10.0.0.1")
    assert scan.is_private_ipv4("172.16.0.1")
    assert scan.is_private_ipv4("192.168.1.10")
    assert not scan.is_private_ipv4("127.0.0.1")
    assert not scan.is_private_ipv4("192.0.2.1")
    assert scan.private_ips_in_line("http://10.1.2.3:8080") == ["10.1.2.3"]
    assert scan.private_ips_in_line("http://192.0.2.1:8080") == []


def test_blocked_domains_parsing_and_detection():
    scan = _load_scan_module()
    assert scan.parse_blocked_domains("perso.invalid, Autre.EXAMPLE") == [
        "perso.invalid",
        "autre.example",
    ]
    assert scan.parse_blocked_domains(None) == []
    line = "PUBLIC_URL: https://app.perso.invalid"
    assert scan.blocked_domains_in_line(line, [_EXAMPLE_BLOCKED]) == [_EXAMPLE_BLOCKED]
    issues = scan.compose_readme_violations(
        "docker-compose.yml",
        1,
        line,
        blocked_domains=[_EXAMPLE_BLOCKED],
    )
    assert len(issues) == 1
    assert _EXAMPLE_BLOCKED in issues[0]
    assert scan.compose_readme_violations(
        "backend/foo.py",
        1,
        line,
        blocked_domains=[_EXAMPLE_BLOCKED],
    ) == []


def test_secret_scan_script_passes_on_repository():
    result = subprocess.run(
        [sys.executable, str(_SCAN_SCRIPT)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout

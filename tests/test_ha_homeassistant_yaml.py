"""Charge les YAML Home Assistant du dépôt et refuse IP privées, secrets et domaines personnels."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1] / "homeassistant"

_PRIVATE_IPV4 = re.compile(
    r"\b(?:"
    r"10(?:\.\d{1,3}){3}"
    r"|127(?:\.\d{1,3}){3}"
    r"|169\.254(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r")\b"
)

_TOKEN_VALUE = r"[A-Za-z0-9_\-+/=.]{20,}"
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN (?:EC |RSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"ghp_[A-Za-z0-9]{36,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{22,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,}"),
    re.compile(rf"(?i)(?:api[_-]?key|token)={_TOKEN_VALUE}"),
    re.compile(rf"""(?i)(?:api[_-]?key|token)\s*=\s*['"]{_TOKEN_VALUE}['"]"""),
    re.compile(rf"(?i)[?&](?:api[_-]?key|token)={_TOKEN_VALUE}"),
    re.compile(r"\bBearer\s+mt_[A-Za-z0-9_\-+/=]{16,}\b"),
]

_URL_HOST = re.compile(r"https?://([^/\"'\s#]+)", re.IGNORECASE)

# Hôtes autorisés dans les URL littérales des YAML (le reste = domaine personnel ou hors dépôt).
_ALLOWED_URL_HOSTS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "localhost",
        "tmdb.org",
        "themoviedb.org",
        "www.themoviedb.org",
    }
)
_PERSONAL_HOST_SUFFIXES = (".local", ".lan", ".home", ".internal")
_PERSONAL_HOST_MARKERS = (
    "duckdns.org",
    "no-ip.",
    "synology.me",
)


def _ha_loader() -> type[yaml.SafeLoader]:
    class Loader(yaml.SafeLoader):
        pass

    def secret(loader: Loader, node: yaml.Node) -> str:
        return loader.construct_scalar(node)

    def include(loader: Loader, node: yaml.Node) -> object:
        return loader.construct_scalar(node)

    Loader.add_constructor("!secret", secret)
    Loader.add_constructor("!include", include)
    Loader.add_constructor("!include_dir_list", include)
    Loader.add_constructor("!include_dir_named", include)
    Loader.add_constructor("!include_dir_merge_list", include)
    Loader.add_constructor("!include_dir_merge_named", include)
    return Loader


def _yaml_paths() -> list[Path]:
    return sorted(p for p in _ROOT.rglob("*.yaml") if p.is_file())


def test_homeassistant_yaml_files_parse():
    loader = _ha_loader()
    for path in _yaml_paths():
        text = path.read_text(encoding="utf-8")
        data = yaml.load(text, Loader=loader)
        assert data is not None or not text.strip(), path.relative_to(_ROOT)


def _host_allowed(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if host in _ALLOWED_URL_HOSTS:
        return True
    if host.endswith(".example.com") or host.endswith(".example.org"):
        return True
    return False


def _host_is_personal(hostname: str) -> bool:
    host = hostname.lower()
    if any(host.endswith(suffix) for suffix in _PERSONAL_HOST_SUFFIXES):
        return True
    return any(marker in host for marker in _PERSONAL_HOST_MARKERS)


def test_homeassistant_yaml_has_no_private_ip_secret_or_personal_domain():
    failures: list[str] = []
    for path in _yaml_paths():
        rel = path.relative_to(_ROOT)
        is_secrets_example = rel.name == "secrets.yaml"
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not is_secrets_example and _PRIVATE_IPV4.search(line):
                failures.append(f"{rel}:{lineno}: IP privée")
            if not is_secrets_example:
                for pattern in _SECRET_PATTERNS:
                    if pattern.search(line):
                        failures.append(f"{rel}:{lineno}: motif secret")
                        break
            for match in _URL_HOST.finditer(line):
                raw_host = match.group(1)
                host = raw_host.split("@")[-1].split(":")[0]
                if _PRIVATE_IPV4.fullmatch(host):
                    if not is_secrets_example:
                        failures.append(f"{rel}:{lineno}: IP privée dans URL ({host})")
                    continue
                if _host_is_personal(host):
                    failures.append(f"{rel}:{lineno}: domaine personnel ({host})")
                elif not _host_allowed(host):
                    failures.append(f"{rel}:{lineno}: hôte URL non autorisé ({host})")
    assert failures == []

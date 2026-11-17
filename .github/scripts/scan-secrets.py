#!/usr/bin/env python3
"""Scan des fichiers git pour motifs de secrets (allowlist minimale)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = ROOT / ".github" / "secret-scan-allowlist.txt"
BLOCKED_DOMAINS_ENV = "SECRET_SCAN_BLOCKED_DOMAINS"
SCAN_ROOT_ENV = "SCAN_ROOT"
MIRROR_PATTERNS_ENV = "MIRROR_BLOCKED_PATTERNS"
MIRROR_STRICT_UPTOBOX_ENV = "MIRROR_STRICT_UPTOBOX"

# Contextes « uptobox = hébergeur interdit » déjà présents dans l’arbre source.
_UPTOBOX_ALLOWED_MARKERS = (
    "BLOCKED_SOURCE_HOSTERS",
    "blocked hoster",
    "hébergeur interdit",
    "_KEEP_HOSTERS",
    "hoster_rank",
    '!= "uptobox"',
    "!= 'uptobox'",
    '== "uptobox"',
    "== 'uptobox'",
    '.casefold() == "uptobox"',
    "skips_uptobox",
    "omits_uptobox",
    "filters_uptobox",
    "stays_blocked",
    "parse_hoster",
    '("uptobox"',
    "('uptobox'",
    "frozenset({\"uptobox\"}",
)

BINARY_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".ttf",
    ".woff",
    ".woff2",
    ".eot",
    ".pdf",
    ".zip",
    ".gz",
    ".br",
}

# Valeur de jeton : caractères usuels (base64/url), sans parenthèses ni code Python.
_TOKEN_VALUE = r"[A-Za-z0-9_\-+/=.]{20,}"
_KEY_PREFIX = r"(?:api[_-]?key|token)"

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("clé privée PEM", re.compile(r"-----BEGIN (?:EC |RSA |OPENSSH )?PRIVATE KEY-----")),
    ("jeton GitHub ghp_", re.compile(r"ghp_[A-Za-z0-9]{36,}")),
    ("jeton GitHub github_pat_", re.compile(r"github_pat_[A-Za-z0-9_]{22,}")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,}")),
    (
        "apikey/token littéral long (conf)",
        re.compile(rf"(?i){_KEY_PREFIX}={_TOKEN_VALUE}"),
    ),
    (
        "apikey/token littéral long (cité)",
        re.compile(rf"""(?i){_KEY_PREFIX}\s*=\s*['"]{_TOKEN_VALUE}['"]"""),
    ),
    (
        "apikey/token littéral long (URL)",
        re.compile(rf"(?i)[?&](?:api[_-]?key|token)={_TOKEN_VALUE}"),
    ),
]

_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)

# Plages documentées (RFC 5737) — autorisées dans README et Compose.
_DOC_NET_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")


def parse_blocked_domains(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def load_blocked_domains_from_env() -> list[str]:
    return parse_blocked_domains(os.environ.get(BLOCKED_DOMAINS_ENV))


def blocked_domains_in_line(line: str, domains: list[str]) -> list[str]:
    if not domains:
        return []
    lower = line.lower()
    return [domain for domain in domains if domain in lower]


def is_documentation_ip(ip: str) -> bool:
    if ip == "127.0.0.1":
        return True
    return any(ip.startswith(prefix) for prefix in _DOC_NET_PREFIXES)


def is_private_ipv4(ip: str) -> bool:
    if is_documentation_ip(ip):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if octets[0] == 10:
        return True
    if octets[0] == 172 and 16 <= octets[1] <= 31:
        return True
    if octets[0] == 192 and octets[1] == 168:
        return True
    return False


def private_ips_in_line(line: str) -> list[str]:
    return [ip for ip in _IPV4.findall(line) if is_private_ipv4(ip)]


def is_compose_or_readme(rel: str) -> bool:
    path = Path(rel)
    name = path.name
    if name == "README.md":
        return True
    if "docker-compose" in name and name.endswith((".yml", ".yaml")):
        return True
    return False


def compose_readme_violations(
    rel: str,
    lineno: int,
    line: str,
    *,
    blocked_domains: list[str],
) -> list[str]:
    if not is_compose_or_readme(rel):
        return []
    failures: list[str] = []
    for ip in private_ips_in_line(line):
        failures.append(f"secret-scan: adresse IPv4 privée {ip} dans {rel}:{lineno}")
    for domain in blocked_domains_in_line(line, blocked_domains):
        failures.append(f"secret-scan: domaine bloqué ({domain}) dans {rel}:{lineno}")
    return failures


def resolve_root() -> Path:
    raw = os.environ.get(SCAN_ROOT_ENV, "").strip()
    if raw:
        return Path(raw).resolve()
    return ROOT


def load_allowlist(root: Path) -> list[str]:
    allowlist_path = root / ".github" / "secret-scan-allowlist.txt"
    if not allowlist_path.is_file():
        return []
    items: list[str] = []
    for raw in allowlist_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        items.append(line)
    return items


def git_tracked_files(root: Path) -> list[str]:
    out = subprocess.check_output(["git", "-C", str(root), "ls-files"], text=True)
    return [line for line in out.splitlines() if line]


def export_tree_files(root: Path) -> list[str]:
    files: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/"):
            continue
        files.append(rel)
    return sorted(files)


def list_scan_files(root: Path) -> list[str]:
    if os.environ.get(SCAN_ROOT_ENV, "").strip():
        return export_tree_files(root)
    return git_tracked_files(root)


def load_mirror_patterns() -> list[re.Pattern[str]]:
    raw = os.environ.get(MIRROR_PATTERNS_ENV)
    if raw is None:
        return []
    patterns: list[re.Pattern[str]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        spec = line.strip()
        if not spec or spec.startswith("#"):
            continue
        try:
            patterns.append(re.compile(spec))
        except re.error as exc:
            print(
                f"secret-scan: motif miroir invalide (ligne {lineno}): {exc}",
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
    return patterns


def mirror_uptobox_violation(rel: str, line: str) -> str | None:
    if not re.search(r"uptobox", line, re.IGNORECASE):
        return None
    if rel == ".github/scripts/scan-secrets.py":
        return None
    if rel.startswith("tests/"):
        return None
    if re.search(
        r"(ignored|skipped|interdit|blocked|BLOCKED_SOURCE)",
        line,
        re.IGNORECASE,
    ):
        return None
    if any(marker in line for marker in _UPTOBOX_ALLOWED_MARKERS):
        return None
    # Fichiers de définition / filtre hébergeurs.
    allowed_prefixes = (
        "backend/mediatracker/core/hosters.py",
        "backend/mediatracker/core/selection.py",
        "backend/mediatracker/events/scrub.py",
        "backend/mediatracker/providers/sources/",
        "docs/interne/ajouter-une-source.md",
    )
    if any(rel.startswith(prefix) for prefix in allowed_prefixes):
        return None
    return f"secret-scan: uptobox hors contexte interdit autorisé dans {rel}"


def scan_tracked_files(
    root: Path,
    allowlist: list[str],
    blocked_domains: list[str],
    *,
    mirror_patterns: list[re.Pattern[str]] | None = None,
    mirror_strict_uptobox: bool = False,
) -> list[str]:
    failures: list[str] = []

    def allowed(line: str) -> bool:
        return any(fragment in line for fragment in allowlist)

    mirror_patterns = mirror_patterns or []

    for rel in list_scan_files(root):
        if Path(rel).suffix.lower() in BINARY_SUFFIXES:
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if allowed(line):
                continue
            for label, pattern in PATTERNS:
                if pattern.search(line):
                    failures.append(f"secret-scan: {label} dans {rel}:{lineno}")
                    break
            failures.extend(
                compose_readme_violations(
                    rel,
                    lineno,
                    line,
                    blocked_domains=blocked_domains,
                )
            )
            for index, pattern in enumerate(mirror_patterns, start=1):
                if pattern.search(line):
                    failures.append(
                        f"secret-scan: motif miroir #{index} dans {rel}:{lineno}"
                    )
                    break
            if mirror_strict_uptobox:
                hit = mirror_uptobox_violation(rel, line)
                if hit:
                    failures.append(hit)

    return failures


def main() -> int:
    root = resolve_root()
    allowlist = load_allowlist(root)
    blocked_domains = load_blocked_domains_from_env()
    mirror_patterns = load_mirror_patterns()
    mirror_strict = os.environ.get(MIRROR_STRICT_UPTOBOX_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    failures = scan_tracked_files(
        root,
        allowlist,
        blocked_domains,
        mirror_patterns=mirror_patterns,
        mirror_strict_uptobox=mirror_strict,
    )

    if failures:
        for msg in failures:
            print(msg, file=sys.stderr)
        print(
            "secret-scan: échec — motifs de secrets détectés "
            "(voir allowlist: .github/secret-scan-allowlist.txt)",
            file=sys.stderr,
        )
        return 1

    print("secret-scan: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

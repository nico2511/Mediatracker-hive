#!/usr/bin/env python3
"""Échoue si pip-audit signale une vulnérabilité avec version de correctif disponible."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pip_audit", "-f", "json", str(REPO_ROOT)],
        capture_output=True,
        text=True,
    )
    stdout = proc.stdout.strip()
    if not stdout:
        print(proc.stderr or "pip-audit: sortie vide", file=sys.stderr)
        return proc.returncode if proc.returncode != 0 else 1

    data = json.loads(stdout)
    fixable: list[tuple[str, str, list[str]]] = []
    for dep in data.get("dependencies", []):
        name = dep.get("name", "?")
        for vuln in dep.get("vulns", []):
            fixes = vuln.get("fix_versions") or []
            if fixes:
                fixable.append((name, vuln.get("id", "?"), fixes))

    if fixable:
        print("pip-audit: vulnérabilités corrigeables (montée de version requise):", file=sys.stderr)
        for name, vid, fixes in fixable:
            print(f"  - {name}: {vid} -> {', '.join(fixes)}", file=sys.stderr)
        return 1

    if proc.returncode not in (0, 1):
        print(proc.stderr or "pip-audit a échoué", file=sys.stderr)
        return proc.returncode

    print("pip-audit: aucune vulnérabilité corrigeable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

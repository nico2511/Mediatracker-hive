#!/usr/bin/env python3
"""Vérifie que les chemins de fichiers cités dans docs/architecture.md existent."""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "architecture.md"

FILE_PATTERN = re.compile(
    r"`((?:backend|frontend|docs)/[^\s`]+\.(?:py|ts|vue|md))`"
)
DIR_PATTERN = re.compile(
    r"`((?:backend|frontend)/[^\s`]+/)`"
)


def collect_paths(text: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for pattern in (FILE_PATTERN, DIR_PATTERN):
        for match in pattern.finditer(text):
            rel = match.group(1).split("#")[0].split("?")[0]
            if rel not in seen:
                seen.add(rel)
                out.append(rel)
    return sorted(out)


def main() -> int:
    if not DOC.is_file():
        print(f"MISSING doc: {DOC}", file=sys.stderr)
        return 1
    rel_paths = collect_paths(DOC.read_text(encoding="utf-8"))
    missing: list[str] = []
    ok = 0
    for rel in rel_paths:
        full = REPO / rel
        if full.is_file() or full.is_dir():
            ok += 1
            print(f"OK   {rel}")
        else:
            missing.append(rel)
            print(f"FAIL {rel}")
    print(f"\n{ok} found, {len(missing)} missing (of {len(rel_paths)} checked)")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
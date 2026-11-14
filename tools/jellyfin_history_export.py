#!/usr/bin/env python3
"""Export Jellyfin watch history to a CSV for MediaTracker import.

Reads jellyfin.db in read-only mode (EF Core schema Jellyfin 10.10+).
Standard library only (loads the shared export module from the repo tree).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[1] / "backend/mediatracker/integrations/jellyfin_db_export.py"


def _load_shared():
    spec = importlib.util.spec_from_file_location("jellyfin_db_export", _SHARED)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Module d'export introuvable : {_SHARED}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    mod = _load_shared()
    parser = argparse.ArgumentParser(description="Exporte l'historique vu Jellyfin vers un CSV MediaTracker.")
    parser.add_argument("--jellyfin-db", required=True, type=Path, help="Chemin vers jellyfin.db (copie conseillée)")
    parser.add_argument("--user", help="Nom d'utilisateur Jellyfin (obligatoire si plusieurs comptes)")
    parser.add_argument("--out", required=True, type=Path, help="Fichier CSV de sortie")
    args = parser.parse_args(argv)
    if not args.jellyfin_db.is_file():
        print(f"Fichier introuvable : {args.jellyfin_db}", file=sys.stderr)
        return 2
    try:
        rows = mod.export_history(args.jellyfin_db, user=args.user)
    except mod.JellyfinExportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    mod.write_csv(rows, args.out)
    print(f"{len(rows)} ligne(s) écrites dans {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

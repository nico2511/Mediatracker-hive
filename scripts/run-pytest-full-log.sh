#!/usr/bin/env bash
# Enregistre la sortie pytest et propage le vrai code de sortie (évite tee sans pipefail).
set -o pipefail
OUT="${1:-/opt/cursor/artifacts/t8-pytest-full.log}"
shift || true
python3 -m pytest -q "$@" | tee "$OUT"
code="${PIPESTATUS[0]}"
echo "PYTEST_EXIT:${code}" >>"$OUT"
exit "$code"

"""Vérifie que la doc IA générée est à jour."""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_llms_full_md_is_up_to_date():
    script = REPO_ROOT / "scripts" / "build_ai_doc.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "docs/llms-full.md ou docs/llms.txt obsolètes ; exécutez : python scripts/build_ai_doc.py\n"
        f"stderr: {result.stderr}"
    )

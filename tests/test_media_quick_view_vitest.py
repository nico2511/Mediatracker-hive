"""Lance le test Vitest de MediaQuickView.confirm()."""

import subprocess
from pathlib import Path


def test_media_quick_view_confirm_vitest():
    root = Path(__file__).resolve().parents[1] / "frontend"
    proc = subprocess.run(
        ["npm", "run", "test:unit", "--", "src/components/MediaQuickView.spec.ts"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

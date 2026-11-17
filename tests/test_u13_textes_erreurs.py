"""U13 — libellé Jellyfin et messages d'erreur API côté interface."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONT = ROOT / "frontend" / "src"
API_TS = FRONT / "api.ts"
MEDIA_LIST = FRONT / "views" / "MediaListView.vue"
LIBRARY = FRONT / "views" / "LibraryView.vue"


def test_identified_filter_label_mentions_jellyfin():
    media_list = MEDIA_LIST.read_text(encoding="utf-8")
    library = LIBRARY.read_text(encoding="utf-8")
    assert "Suivi via identification" not in media_list
    assert "Suivi par Jellyfin" in media_list
    assert "Suivi via identification" not in library


def test_user_facing_api_error_mapping():
    script = r"""
import { ApiError, userFacingApiError } from "./api.ts";

const cases = [
  [new ApiError(401, "Unauthorized"), "Session expirée."],
  [
    new ApiError(401, "Nom d'utilisateur ou mot de passe incorrect."),
    "Nom d'utilisateur ou mot de passe incorrect.",
  ],
  [new ApiError(403, "Forbidden"), "Action non autorisée."],
  [new ApiError(404, "Not Found"), "Introuvable."],
  [new ApiError(409, "Conflict"), "Conflit."],
  [new ApiError(422, "value is not a valid integer"), "Données invalides."],
  [new ApiError(429, "Too Many Requests"), "Trop de tentatives."],
  [new ApiError(500, "Internal Server Error"), "Erreur du serveur, consultez le Journal."],
  [new ApiError(404, "La fiche demandée est introuvable."), "La fiche demandée est introuvable."],
  [new TypeError("Failed to fetch"), "Serveur injoignable."],
];

for (const [err, expected] of cases) {
  const got = userFacingApiError(err, "Repli.");
  if (got !== expected) {
    console.error(JSON.stringify({ got, expected, status: err instanceof ApiError ? err.status : null }));
    process.exit(1);
  }
}
"""
    proc = subprocess.run(
        ["npx", "--yes", "tsx", "-e", script],
        cwd=FRONT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

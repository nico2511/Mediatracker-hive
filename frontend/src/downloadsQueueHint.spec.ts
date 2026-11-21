import { describe, expect, it } from "vitest";

import { downloadQueueHint } from "./downloadsQueueHint";

describe("downloadQueueHint", () => {
  it("affiche le message d'erreur et l'heure de reprise pour un pending en attente", () => {
    const hint = downloadQueueHint(
      {
        id: 3,
        state: "pending",
        error:
          "L'instance source Hive ne répond pas (erreur 502). Nouvel essai prévu, le fichier partiel est conservé.",
        error_code: "HIVE_SOURCE_UNREACHABLE",
        next_retry_at: "2026-11-12T14:30:00.000Z",
      },
      [1, 2, 3],
      false,
    );
    expect(hint).toContain("Position 3");
    expect(hint).toContain("instance source Hive");
    expect(hint).toMatch(/prochain essai \d{2}:\d{2}/);
    expect(hint).not.toContain("En attente de débridage");
  });

  it("garde En attente de débridage sans erreur", () => {
    const hint = downloadQueueHint({ id: 1, state: "pending" }, [1], false);
    expect(hint).toBe("Position 1 · En attente de débridage");
  });

  it("affiche le message de nouvelle recherche planifiée avec heure de reprise", () => {
    const hint = downloadQueueHint(
      {
        id: 1,
        state: "pending",
        error: "Nouvelle recherche planifiée.",
        error_code: "REDISCOVER",
        next_retry_at: "2026-11-12T13:45:00.000Z",
      },
      [1],
      false,
    );
    expect(hint).toBe("Position 1 · Nouvelle recherche planifiée. · prochain essai 14:45");
    expect(hint).not.toContain("Wawacity");
  });
});

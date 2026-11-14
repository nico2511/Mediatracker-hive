import { describe, expect, it } from "vitest";

import type { HiveShareUiState } from "./hiveBadges";
import { hiveShareFicheStatus, hiveSharingIntent, shouldPollHiveSharing } from "./hiveShareStatus";

function base(partial: Partial<HiveShareUiState>): HiveShareUiState {
  return {
    shareable: false,
    shared: false,
    exclude_all: true,
    excluded_ruche_ids: [],
    preparing: false,
    transfer_active: false,
    transfer_progress_pct: null,
    hive_keep: false,
    keep: false,
    pull_only: false,
    has_file: true,
    catalog_entry_local: false,
    ...partial,
  };
}

describe("hiveSharingIntent", () => {
  it("ignore shared API : intention = non exclu", () => {
    expect(hiveSharingIntent(base({ exclude_all: false, shareable: false }))).toBe(true);
    expect(hiveSharingIntent(base({ exclude_all: true }))).toBe(false);
  });
});

describe("shouldPollHiveSharing", () => {
  it("poll en préparation", () => {
    expect(shouldPollHiveSharing(base({ preparing: true, exclude_all: false }), { pollExhausted: false })).toBe(true);
  });

  it("poll avec intention et sans annonce locale", () => {
    expect(
      shouldPollHiveSharing(
        base({ exclude_all: false, has_file: true, catalog_entry_local: false, preparing: false }),
        { pollExhausted: false },
      ),
    ).toBe(true);
  });

  it("arrête quand annonce locale prête", () => {
    expect(
      shouldPollHiveSharing(
        base({ exclude_all: false, catalog_entry_local: true, shareable: true }),
        { pollExhausted: false },
      ),
    ).toBe(false);
  });
});

describe("hiveShareFicheStatus", () => {
  it("affiche une erreur sans état chargé", () => {
    const row = hiveShareFicheStatus(null, { requestError: "Base occupée" });
    expect(row.phase).toBe("error");
    expect(row.title).toBe("Erreur de requête");
  });

  it("affiche lecture en cours sans état mais requête active", () => {
    const row = hiveShareFicheStatus(null, { pollBusy: true });
    expect(row.phase).toBe("preparing");
    expect(row.title).toContain("Lecture");
  });
  it("affiche la progression d’empreinte quand la taille est connue", () => {
    const row = hiveShareFicheStatus(
      base({
        preparing: true,
        exclude_all: false,
        fingerprint_partial_size: 32_000_000,
        fingerprint_total_size: 128_000_000,
      }),
      {},
    );
    expect(row.title).toContain("Préparation");
    expect(row.hint).toMatch(/25 %/);
  });

  it("annonce locale sans promettre le pair", () => {
    const row = hiveShareFicheStatus(
      base({ exclude_all: false, shareable: true, catalog_entry_local: true }),
      {},
    );
    expect(row.title).toBe("Annonce locale prête");
    expect(row.hint).toMatch(/pairs/);
  });
});

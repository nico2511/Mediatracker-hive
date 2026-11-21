import { describe, expect, it } from "vitest";

import { hiveBadgesForState, type HiveShareUiState } from "./hiveBadges";

function state(partial: Partial<HiveShareUiState>): HiveShareUiState {
  return {
    shareable: true,
    shared: false,
    exclude_all: false,
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

describe("hiveBadgesForState", () => {
  it("affiche Non partageable seulement sans fichier local", () => {
    const labels = hiveBadgesForState(state({ has_file: false, shareable: false })).map((b) => b.label);
    expect(labels).toContain("Non partageable : à télécharger");
  });

  it("n'affiche pas Non partageable quand le fichier est là mais partage volontairement coupé", () => {
    const labels = hiveBadgesForState(
      state({ has_file: true, shareable: true, shared: false, exclude_all: true }),
    ).map((b) => b.label);
    expect(labels).not.toContain("Non partageable : à télécharger");
  });

  it("badge Partagé en bleu info", () => {
    const badge = hiveBadgesForState(state({ shared: true })).find((b) => b.key === "shared");
    expect(badge?.tone).toBe("info");
  });
});

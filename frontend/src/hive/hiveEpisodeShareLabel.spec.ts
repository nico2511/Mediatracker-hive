import { describe, expect, it } from "vitest";

import type { HiveShareUiState } from "./hiveBadges";
import { hiveEpisodeShareChip } from "./hiveEpisodeShareLabel";

const base: HiveShareUiState = {
  shareable: true,
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
};

describe("hiveEpisodeShareChip", () => {
  it("retourne null sans fichier", () => {
    expect(hiveEpisodeShareChip({ ...base, has_file: false })).toBeNull();
  });

  it("affiche Partagé quand shared", () => {
    const chip = hiveEpisodeShareChip({ ...base, shared: true, exclude_all: false });
    expect(chip?.label).toBe("Partagé");
  });

  it("affiche empreinte en préparation", () => {
    const chip = hiveEpisodeShareChip({
      ...base,
      preparing: true,
      fingerprint_partial_size: 390,
      fingerprint_total_size: 1000,
    });
    expect(chip?.label).toBe("Empreinte 39 %");
  });
});

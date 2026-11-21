import { describe, expect, it } from "vitest";

import type { HiveShareUiState } from "./hiveBadges";
import { hiveEpisodeShareChip, hiveEpisodeShareChips } from "./hiveEpisodeShareLabel";

const base: HiveShareUiState = {
  shareable: true,
  shared: false,
  exclude_all: true,
  excluded_ruche_ids: [],
  preparing: false,
  transfer_active: false,
  transfer_progress_pct: null,
  transfer_outbound_active: false,
  transfer_outbound_progress_pct: null,
  received_via_hive: false,
  hive_keep: false,
  keep: false,
  pull_only: false,
  has_file: true,
  catalog_entry_local: false,
};

describe("hiveEpisodeShareChips", () => {
  it("retourne null sans fichier hors transfert entrant", () => {
    expect(hiveEpisodeShareChips({ ...base, has_file: false })).toEqual([]);
  });

  it("affiche le transfert entrant sans fichier", () => {
    const chips = hiveEpisodeShareChips({
      ...base,
      has_file: false,
      transfer_active: true,
      transfer_progress_pct: 38,
    });
    expect(chips[0]?.labelShort).toBe("Entrant 38 %");
    expect(chips[0]?.labelLong).toBe("Transfert entrant 38 %");
  });

  it("affiche Partagé quand shared", () => {
    const chips = hiveEpisodeShareChips({ ...base, shared: true, exclude_all: false });
    expect(chips.some((c) => c.key === "shared")).toBe(true);
  });

  it("reçu via Hive masqué si reparatagé", () => {
    const chips = hiveEpisodeShareChips({
      ...base,
      received_via_hive: true,
      shared: true,
      exclude_all: false,
    });
    expect(chips.some((c) => c.key === "received")).toBe(false);
    expect(chips.some((c) => c.key === "shared")).toBe(true);
  });

  it("affiche Reçu via Hive", () => {
    const chips = hiveEpisodeShareChips({ ...base, received_via_hive: true, exclude_all: true });
    expect(chips[0]?.labelShort).toBe("Reçu via Hive");
    expect(chips[0]?.tone).toBe("ok");
  });

  it("ajoute envoi sortant en plus de Partagé", () => {
    const chips = hiveEpisodeShareChips({
      ...base,
      shared: true,
      exclude_all: false,
      transfer_outbound_active: true,
      transfer_outbound_progress_pct: 61,
    });
    expect(chips.map((c) => c.key)).toEqual(["shared", "outbound"]);
    expect(chips[1]?.labelShort).toBe("Envoi 61 %");
  });

  it("affiche empreinte en préparation", () => {
    const chip = hiveEpisodeShareChip({
      ...base,
      preparing: true,
      fingerprint_partial_size: 390,
      fingerprint_total_size: 1000,
    });
    expect(chip?.labelShort).toBe("Empreinte 39 %");
  });
});
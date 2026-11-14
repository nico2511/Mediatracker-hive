import { describe, expect, it } from "vitest";

import {
  hiveCatalogueCardTone,
  hiveCatalogueShowProgressBar,
  hiveCatalogueStateLabel,
} from "./hiveCatalogueCard";

describe("hiveCatalogueCardTone", () => {
  it("mappe les états catalogue connus", () => {
    expect(hiveCatalogueCardTone("error")).toBe("danger");
    expect(hiveCatalogueCardTone("transfer")).toBe("honey");
    expect(hiveCatalogueCardTone("library")).toBe("info");
    expect(hiveCatalogueCardTone("unknown")).toBe("muted");
  });
});

describe("hiveCatalogueStateLabel", () => {
  const base = {
    card_state: "available",
    state_label: "Disponible",
    state_label_short: "Dispo.",
    progress_pct: null,
  };

  it("utilise le libellé court en mode compact", () => {
    expect(hiveCatalogueStateLabel(base, true)).toBe("Dispo.");
  });

  it("utilise le libellé long hors compact", () => {
    expect(hiveCatalogueStateLabel(base, false)).toBe("Disponible");
  });

  it("affiche le pourcentage en transfert", () => {
    expect(
      hiveCatalogueStateLabel(
        { ...base, card_state: "transfer", state_label: "Transfert", progress_pct: 42.4 },
        false,
      ),
    ).toBe("Transfert 42 %");
  });

  it("affiche le pourcentage en préparation (compact)", () => {
    expect(
      hiveCatalogueStateLabel(
        {
          ...base,
          card_state: "preparing",
          state_label_short: "Prép.",
          progress_pct: 12,
        },
        true,
      ),
    ).toBe("Prép. 12 %");
  });
});

describe("hiveCatalogueShowProgressBar", () => {
  it("n’affiche la barre que pour transfert ou préparation avec un pourcentage", () => {
    expect(hiveCatalogueShowProgressBar("transfer", 10)).toBe(true);
    expect(hiveCatalogueShowProgressBar("preparing", 0)).toBe(true);
    expect(hiveCatalogueShowProgressBar("transfer", null)).toBe(false);
    expect(hiveCatalogueShowProgressBar("available", 50)).toBe(false);
  });
});

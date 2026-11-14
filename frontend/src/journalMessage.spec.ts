import { describe, expect, it } from "vitest";

import { journalDetail } from "./journalMessage";

describe("journalDetail", () => {
  it("affiche une bascule de source lisible", () => {
    expect(
      journalDetail("download.source_switched", {
        message: "Bascule de source : Wawacity vers Comet",
        from_source: "wawacity",
        to_source: "comet",
      }),
    ).toBe("Bascule de source : Wawacity vers Comet");
  });

  it("reconstruit le libellé depuis les labels si message absent", () => {
    expect(
      journalDetail("download.source_switched", {
        from_label: "Hive",
        to_label: "Torrentio",
      }),
    ).toBe("Bascule de source : Hive vers Torrentio");
  });

  it("affiche une tentative de source", () => {
    expect(
      journalDetail("download.source_attempt", {
        message: "Comet : aucun résultat.",
      }),
    ).toBe("Comet : aucun résultat.");
  });

  it("affiche les événements de transfert Hive historiques", () => {
    expect(journalDetail("hive.transfer.verified", {})).toContain("historique");
    expect(
      journalDetail("hive.transfer.failed", { message: "Empreinte incorrecte." }),
    ).toBe("Empreinte incorrecte.");
  });

  it("affiche les événements d'empreinte Hive", () => {
    expect(journalDetail("hive.fingerprint.started", {})).toContain("empreinte");
    expect(journalDetail("hive.fingerprint.done", {})).toContain("enregistrée");
    expect(
      journalDetail("hive.fingerprint.error", {
        message: "Fichier modifié pendant le calcul.",
      }),
    ).toBe("Fichier modifié pendant le calcul.");
  });
});

import { describe, expect, it } from "vitest";

import {
  hiveReceivedAcceptNote,
  hiveReceivedAcceptNoteAfterReload,
  hiveReceivedAcceptNotePair,
  hiveReceivedFollowUpLine,
} from "./hiveRequests";

describe("hive received follow-up copy", () => {
  it("décrit l’empreinte et le partage après acceptation", () => {
    expect(hiveReceivedFollowUpLine({ status: "preparing" })).toMatch(/Empreinte/);
    expect(hiveReceivedFollowUpLine({ status: "sharing" })).toMatch(/Partage en cours/);
    expect(hiveReceivedFollowUpLine({ status: "blocked" })).toMatch(/Adresse publique/);
    expect(hiveReceivedAcceptNote({ status: "preparing" })).toBe(hiveReceivedFollowUpLine({ status: "preparing" }));
  });

  it("raccourcit les textes de suivi sur téléphone", () => {
    expect(hiveReceivedFollowUpLine({ status: "sharing" }, { compact: true })).toBe("Partage en cours.");
    expect(hiveReceivedFollowUpLine({ status: "blocked" }, { compact: true })).toBe(
      "Adresse publique Hive manquante.",
    );
  });

  it("reprend le libellé de suivi dès l’acceptation, y compris la version courte", () => {
    expect(hiveReceivedAcceptNote({ status: "pending" })).toBe("Demande acceptée.");
    expect(hiveReceivedAcceptNote({ status: "sharing" })).toBe(
      "Partage en cours : synchronisation du catalogue local.",
    );
    expect(hiveReceivedAcceptNote({ status: "sharing" }, { compact: true })).toBe("Partage en cours.");
    expect(hiveReceivedAcceptNote({ status: "blocked" }, { compact: true })).toBe(
      "Adresse publique Hive manquante.",
    );
    expect(hiveReceivedAcceptNote(undefined)).toBe("Demande acceptée.");
    expect(
      hiveReceivedAcceptNoteAfterReload("req-1", [{ request_id: "req-1", status: "preparing" }]),
    ).toBe("Empreinte du fichier en cours.");
    expect(
      hiveReceivedAcceptNoteAfterReload("req-1", [{ request_id: "req-1", status: "sharing" }], {
        compact: true,
      }),
    ).toBe("Partage en cours.");
    expect(hiveReceivedAcceptNoteAfterReload("req-1", [])).toBe("Demande acceptée.");
    expect(hiveReceivedAcceptNotePair("req-1", [{ request_id: "req-1", status: "blocked" }])).toEqual({
      text: "Adresse publique Hive manquante : l’offre ne peut pas encore être publiée.",
      compact: "Adresse publique Hive manquante.",
    });
  });
});

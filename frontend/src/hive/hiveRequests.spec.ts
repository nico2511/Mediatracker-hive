import { describe, expect, it } from "vitest";

import {
  hiveRequestKindLine,
  hiveRequestMineBadge,
  hiveRequestTitleLine,
  hiveWantedStatusLine,
  hiveWantedTakeLabel,
  type HiveMyRequestItem,
} from "./hiveRequests";

const base: HiveMyRequestItem = {
  media_id: 1,
  media_item_id: 2,
  work_key: "wk-demo",
  title: "Les Abeilles du Nord",
  type: "film",
  season: null,
  episode: null,
  status: "pending",
  hive_request_available: false,
};

describe("hiveRequests", () => {
  it("formate le titre série avec épisode", () => {
    expect(
      hiveRequestTitleLine({
        ...base,
        type: "series",
        title: "La Ruche d'Argent",
        season: 2,
        episode: 10,
      }),
    ).toBe("La Ruche d'Argent · S2 · E10");
  });

  it("expose le badge demandé hors prompt", () => {
    expect(hiveRequestMineBadge("pending")).toBe("Demandé à la ruche");
    expect(hiveRequestMineBadge("prompt")).toBeNull();
    expect(hiveRequestMineBadge("retracted")).toBeNull();
  });

  it("décrit un prompt comme suivi en attente", () => {
    expect(hiveRequestKindLine({ ...base, status: "prompt", type: "film" })).toContain("suivi en attente");
  });

  it("formate le statut Recherché", () => {
    expect(hiveWantedStatusLine(1)).toContain("1 demandeur anonyme");
    expect(hiveWantedStatusLine(3)).toContain("3 demandeurs");
  });

  it("choisit le libellé de prise en charge", () => {
    expect(hiveWantedTakeLabel(false)).toBe("Je m'en occupe");
    expect(hiveWantedTakeLabel(true)).toBe("Ne plus m'en occuper");
  });
});
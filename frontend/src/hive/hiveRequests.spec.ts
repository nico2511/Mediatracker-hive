import { describe, expect, it } from "vitest";

import {
  groupHiveMyRequests,
  hiveFicheSeriesDemandLine,
  hiveMyRequestAggregateLine,
  hiveRequestKindLine,
  hiveRequestMineBadge,
  hiveRequestTitleLine,
  hiveWantedStatusLine,
  hiveWantedTakeLabel,
  sortHiveMyRequestGroups,
  type HiveMyRequestItem,
} from "./hiveRequests";

const base: HiveMyRequestItem = {
  media_id: 1,
  media_item_id: 2,
  work_key: "wk-demo",
  title: "Film Exemple C",
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
        title: "Série Exemple D",
        season: 2,
        episode: 10,
      }),
    ).toBe("Série Exemple D · S2 · E10");
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

  it("formate la ligne DEMANDE agrégée de la fiche série", () => {
    expect(
      hiveFicheSeriesDemandLine([
        { ...base, media_item_id: 1, status: "pending" },
        { ...base, media_item_id: 2, status: "transfer", type: "series", season: 1, episode: 2 },
      ]),
    ).toBe("2 épisodes demandés · 1 en transfert");
  });

  it("agrège les compteurs non nuls", () => {
    expect(
      hiveMyRequestAggregateLine({ pending: 2, transfer: 1, prompt: 0, retracted: 1 }),
    ).toBe("2 en attente · 1 en transfert · 1 retrait mémorisé");
  });

  it("trie les fiches avec transfert avant les autres", () => {
    const groups = groupHiveMyRequests([
      {
        ...base,
        media_id: 1,
        media_item_id: 1,
        title: "Film Exemple E",
        requested_at: "2026-02-01T00:00:00Z",
      },
      {
        ...base,
        media_id: 2,
        media_item_id: 2,
        title: "Série Exemple F",
        type: "series",
        season: 1,
        episode: 1,
        status: "transfer",
        requested_at: "2026-01-01T00:00:00Z",
      },
    ]);
    const sorted = sortHiveMyRequestGroups(groups);
    expect(sorted[0].title).toBe("Série Exemple F");
  });
});
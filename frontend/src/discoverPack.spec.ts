import { describe, expect, it } from "vitest";

import { filterDiscoverDeck, flattenHomeRows, mergeReloadDeck, moveCardToEnd } from "./discoverPack";
import type { DiscoverCard } from "./discoverTypes";

const sample = (id: number, type: string, followed = false): DiscoverCard => ({
  tmdb_id: id,
  media_type: type,
  title: `Titre ${id}`,
  followed,
});

describe("discoverPack", () => {
  it("déduplique les rangées home", () => {
    const home = {
      cinema_films: [sample(1, "film")],
      streaming_films: [sample(1, "film"), sample(2, "film")],
      physical_films: [],
      new_series: [sample(3, "series")],
      on_air_series: [],
    };
    const rows = flattenHomeRows(home);
    expect(rows.map((row) => row.tmdb_id)).toEqual([1, 2, 3]);
  });

  it("filtre passer et suivis", () => {
    const deck = [sample(1, "film"), sample(2, "series", true), sample(3, "film")];
    const filtered = filterDiscoverDeck(deck, {
      passKeys: new Set(["film:1"]),
      mediaFilter: "all",
      unfollowedOnly: true,
    });
    expect(filtered.map((row) => row.tmdb_id)).toEqual([3]);
  });

  it("envoie Plus tard en fin de pile", () => {
    const deck = [sample(1, "film"), sample(2, "film")];
    const moved = moveCardToEnd(deck, sample(1, "film"));
    expect(moved.map((row) => row.tmdb_id)).toEqual([2, 1]);
  });

  it("conserve les cartes Plus tard après rechargement", () => {
    const rows = [sample(1, "film"), sample(2, "film"), sample(3, "film")];
    const previous = [sample(1, "film"), sample(2, "film"), sample(3, "film")];
    const later = new Set(["film:1"]);
    const deck = mergeReloadDeck(
      rows,
      { passKeys: new Set(), mediaFilter: "all", unfollowedOnly: true },
      later,
      previous,
    );
    expect(deck.map((row) => row.tmdb_id)).toEqual([2, 3, 1]);
  });
});
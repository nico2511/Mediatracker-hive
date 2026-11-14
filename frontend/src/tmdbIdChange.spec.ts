import { describe, expect, it } from "vitest";

import { formatEpisodeSlot, readTmdbIdChangePreview } from "./tmdbIdChange";

describe("readTmdbIdChangePreview", () => {
  it("retourne null sans needs_confirm", () => {
    expect(readTmdbIdChangePreview({ detail: "x" })).toBeNull();
  });

  it("retourne null si tmdb_id_change partiel (sans jeton TMDB)", () => {
    expect(
      readTmdbIdChangePreview({
        needs_confirm: true,
        tmdb_preview_available: false,
        tmdb_id_change: { previous_tmdb_id: 100, new_tmdb_id: 200 },
      }),
    ).toBeNull();
    expect(
      readTmdbIdChangePreview({
        needs_confirm: true,
        previous_tmdb_id: 100,
        new_tmdb_id: 200,
      }),
    ).toBeNull();
  });

  it("lit le bloc tmdb_id_change", () => {
    const preview = readTmdbIdChangePreview({
      needs_confirm: true,
      tmdb_id_change: {
        previous_tmdb_id: 100,
        new_tmdb_id: 200,
        removed_if_rebuild: [{ season: 2, episode: 9, title: "titre fictif", item_id: 1 }],
        files_to_reassociate: [],
        orphans_if_keep: [],
      },
    });
    expect(preview?.new_tmdb_id).toBe(200);
    expect(preview?.removed_if_rebuild[0].season).toBe(2);
  });
});

describe("formatEpisodeSlot", () => {
  it("formate SxEy avec titre", () => {
    expect(formatEpisodeSlot({ season: 2, episode: 9, title: "titre fictif", item_id: 1 })).toBe(
      "S2E9 — titre fictif",
    );
  });
});

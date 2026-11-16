import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  DISCOVER_CACHE_KEY,
  DISCOVER_MAX_ENTRIES,
  DISCOVER_PASS_KEY,
  DISCOVER_TTL_MS,
  discoverEntryKey,
  markDiscoverPass,
  openDiscoverStores,
  pruneTimedRows,
  readDiscoverCache,
  readPassKeys,
  removeDiscoverCache,
  upsertDiscoverCache,
} from "./discoverCache";
import type { DiscoverCard } from "./discoverTypes";

const card = (id: number, media_type: string): DiscoverCard => ({
  tmdb_id: id,
  media_type,
  title: `Titre ${id}`,
});

describe("discoverCache", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.useRealTimers();
  });

  it("purge les entrées expirées et respecte le plafond", () => {
    const now = 1_700_000_000_000;
    const rows = Array.from({ length: DISCOVER_MAX_ENTRIES + 5 }, (_, index) => ({
      key: `film:${index}`,
      ts: now - index * 1000,
    }));
    const pruned = pruneTimedRows(rows, now);
    expect(pruned).toHaveLength(DISCOVER_MAX_ENTRIES);
    const expired = [{ key: "film:1", ts: now - DISCOVER_TTL_MS - 1 }];
    expect(pruneTimedRows(expired, now)).toEqual([]);
  });

  it("mémorise Passer et retire du cache au suivi", () => {
    const demo = card(42, "film");
    upsertDiscoverCache(demo);
    expect(readDiscoverCache()).toHaveLength(1);
    markDiscoverPass(demo);
    expect(readPassKeys().has(discoverEntryKey("film", 42))).toBe(true);
    removeDiscoverCache(demo);
    expect(readDiscoverCache()).toHaveLength(0);
  });

  it("purge à l’ouverture", () => {
    const now = Date.now();
    localStorage.setItem(
      DISCOVER_PASS_KEY,
      JSON.stringify([{ key: "film:9", ts: now - DISCOVER_TTL_MS - 1 }]),
    );
    localStorage.setItem(
      DISCOVER_CACHE_KEY,
      JSON.stringify([{ key: "series:1", ts: now - DISCOVER_TTL_MS - 1, card: card(1, "series") }]),
    );
    openDiscoverStores(now);
    expect(readPassKeys(now).size).toBe(0);
    expect(readDiscoverCache()).toEqual([]);
  });
});
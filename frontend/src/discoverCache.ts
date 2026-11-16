import type { DiscoverCard } from "./discoverTypes";

export const DISCOVER_CACHE_KEY = "mt.discover.cache.v1";
export const DISCOVER_PASS_KEY = "mt.discover.pass.v1";
export const DISCOVER_GESTURES_KEY = "mt.discover.gesturesSeen.v1";

export const DISCOVER_TTL_MS = 24 * 60 * 60 * 1000;
export const DISCOVER_MAX_ENTRIES = 200;

export interface DiscoverCacheRow {
  key: string;
  ts: number;
  card: DiscoverCard;
}

export interface DiscoverPassRow {
  key: string;
  ts: number;
}

export function discoverEntryKey(mediaType: string, tmdbId: number): string {
  return `${mediaType}:${tmdbId}`;
}

export function pruneTimedRows<T extends { ts: number }>(rows: T[], now = Date.now()): T[] {
  const alive = rows.filter((row) => now - row.ts < DISCOVER_TTL_MS);
  alive.sort((a, b) => b.ts - a.ts);
  return alive.slice(0, DISCOVER_MAX_ENTRIES);
}

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) {
      return fallback;
    }
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // ignore quota errors
  }
}

export function openDiscoverStores(now = Date.now()): void {
  const cache = pruneTimedRows(readJson<DiscoverCacheRow[]>(DISCOVER_CACHE_KEY, []), now);
  writeJson(DISCOVER_CACHE_KEY, cache);
  const pass = pruneTimedRows(readJson<DiscoverPassRow[]>(DISCOVER_PASS_KEY, []), now);
  writeJson(DISCOVER_PASS_KEY, pass);
}

export function readDiscoverCache(): DiscoverCacheRow[] {
  return readJson<DiscoverCacheRow[]>(DISCOVER_CACHE_KEY, []);
}

export function upsertDiscoverCache(card: DiscoverCard, now = Date.now()): void {
  const key = discoverEntryKey(card.media_type, card.tmdb_id);
  const rows = readDiscoverCache().filter((row) => row.key !== key);
  rows.push({ key, ts: now, card });
  writeJson(DISCOVER_CACHE_KEY, pruneTimedRows(rows, now));
}

export function removeDiscoverCache(card: DiscoverCard): void {
  const key = discoverEntryKey(card.media_type, card.tmdb_id);
  const rows = readDiscoverCache().filter((row) => row.key !== key);
  writeJson(DISCOVER_CACHE_KEY, rows);
}

export function readPassKeys(now = Date.now()): Set<string> {
  const rows = pruneTimedRows(readJson<DiscoverPassRow[]>(DISCOVER_PASS_KEY, []), now);
  writeJson(DISCOVER_PASS_KEY, rows);
  return new Set(rows.map((row) => row.key));
}

export function markDiscoverPass(card: DiscoverCard, now = Date.now()): void {
  const key = discoverEntryKey(card.media_type, card.tmdb_id);
  const rows = readJson<DiscoverPassRow[]>(DISCOVER_PASS_KEY, []).filter((row) => row.key !== key);
  rows.push({ key, ts: now });
  writeJson(DISCOVER_PASS_KEY, pruneTimedRows(rows, now));
}

export function gesturesHelpSeen(): boolean {
  try {
    return localStorage.getItem(DISCOVER_GESTURES_KEY) === "1";
  } catch {
    return false;
  }
}

export function markGesturesHelpSeen(): void {
  try {
    localStorage.setItem(DISCOVER_GESTURES_KEY, "1");
  } catch {
    // ignore
  }
}
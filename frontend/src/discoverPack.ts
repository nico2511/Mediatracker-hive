import type { DiscoverCard, DiscoverMediaFilter } from "./discoverTypes";
import { discoverEntryKey } from "./discoverCache";

export interface TmdbHomePayload {
  cinema_films: DiscoverCard[];
  streaming_films: DiscoverCard[];
  physical_films: DiscoverCard[];
  new_series: DiscoverCard[];
  on_air_series: DiscoverCard[];
}

const HOME_KEYS: (keyof TmdbHomePayload)[] = [
  "cinema_films",
  "streaming_films",
  "physical_films",
  "new_series",
  "on_air_series",
];

export function flattenHomeRows(home: TmdbHomePayload): DiscoverCard[] {
  const out: DiscoverCard[] = [];
  const seen = new Set<string>();
  for (const key of HOME_KEYS) {
    for (const card of home[key] || []) {
      const id = discoverEntryKey(card.media_type, card.tmdb_id);
      if (seen.has(id)) {
        continue;
      }
      seen.add(id);
      out.push(card);
    }
  }
  return out;
}

export function filterDiscoverDeck(
  cards: DiscoverCard[],
  opts: { passKeys: Set<string>; mediaFilter: DiscoverMediaFilter; unfollowedOnly: boolean },
): DiscoverCard[] {
  return cards.filter((card) => {
    const key = discoverEntryKey(card.media_type, card.tmdb_id);
    if (opts.passKeys.has(key)) {
      return false;
    }
    if (opts.unfollowedOnly && card.followed) {
      return false;
    }
    if (opts.mediaFilter === "film" && card.media_type !== "film") {
      return false;
    }
    if (opts.mediaFilter === "series" && card.media_type !== "series") {
      return false;
    }
    return true;
  });
}

export function moveCardToEnd(deck: DiscoverCard[], card: DiscoverCard): DiscoverCard[] {
  const key = discoverEntryKey(card.media_type, card.tmdb_id);
  const rest = deck.filter((row) => discoverEntryKey(row.media_type, row.tmdb_id) !== key);
  return [...rest, card];
}

export function dropCard(deck: DiscoverCard[], card: DiscoverCard): DiscoverCard[] {
  const key = discoverEntryKey(card.media_type, card.tmdb_id);
  return deck.filter((row) => discoverEntryKey(row.media_type, row.tmdb_id) !== key);
}

export function reorderDeckWithLater(deck: DiscoverCard[], laterKeys: Iterable<string>): DiscoverCard[] {
  let next = [...deck];
  for (const key of laterKeys) {
    const card = next.find((row) => discoverEntryKey(row.media_type, row.tmdb_id) === key);
    if (card) {
      next = moveCardToEnd(next, card);
    }
  }
  return next;
}

export function mergeReloadDeck(
  rows: DiscoverCard[],
  opts: { passKeys: Set<string>; mediaFilter: DiscoverMediaFilter; unfollowedOnly: boolean },
  laterKeys: Set<string>,
  previous: DiscoverCard[],
): DiscoverCard[] {
  const rowByKey = new Map(rows.map((card) => [discoverEntryKey(card.media_type, card.tmdb_id), card]));
  const carry = new Map<string, DiscoverCard>();
  for (const card of [...previous, ...rows]) {
    carry.set(discoverEntryKey(card.media_type, card.tmdb_id), card);
  }
  let deck = filterDiscoverDeck(rows, opts);
  for (const key of laterKeys) {
    if (opts.passKeys.has(key)) {
      continue;
    }
    const fresh = rowByKey.get(key) ?? carry.get(key);
    if (!fresh) {
      continue;
    }
    if (!deck.some((row) => discoverEntryKey(row.media_type, row.tmdb_id) === key)) {
      if (
        opts.mediaFilter === "all" ||
        (opts.mediaFilter === "film" && fresh.media_type === "film") ||
        (opts.mediaFilter === "series" && fresh.media_type === "series")
      ) {
        deck.push(fresh);
      }
    }
  }
  return reorderDeckWithLater(deck, laterKeys);
}
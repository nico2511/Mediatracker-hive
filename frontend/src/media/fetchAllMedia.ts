import { assertMediaPageCursor, MAX_MEDIA_PAGES, MediaListCursorError } from "./listPagination";

export type MediaPageFetcher<T> = (
  path: string,
) => Promise<{ items: T[]; next_cursor: string | null }>;

/** Charge toutes les pages médias (limit 200) avec garde-fous curseur. */
export async function fetchAllMedia<T>(
  fetchPage: MediaPageFetcher<T>,
  isStale: () => boolean,
  onPage: (partial: T[]) => void,
): Promise<T[]> {
  const collected: T[] = [];
  let cursor: string | null = null;
  let pageIndex = 0;
  while (true) {
    if (pageIndex >= MAX_MEDIA_PAGES) {
      throw new MediaListCursorError("Pagination médias interrompue : trop de pages.");
    }
    const params = new URLSearchParams({ limit: "200" });
    if (cursor) {
      params.set("cursor", cursor);
    }
    const page = await fetchPage(`/api/v1/media?${params}`);
    if (isStale()) {
      return collected;
    }
    assertMediaPageCursor(cursor, page.next_cursor, page.items.length, pageIndex);
    collected.push(...page.items);
    onPage([...collected]);
    if (!page.next_cursor) {
      break;
    }
    cursor = page.next_cursor;
    pageIndex += 1;
  }
  return collected;
}

export { MAX_MEDIA_PAGES, MediaListCursorError };

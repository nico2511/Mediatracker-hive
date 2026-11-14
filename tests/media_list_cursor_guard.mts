import { assertMediaPageCursor, MAX_MEDIA_PAGES, MediaListCursorError } from "../frontend/src/media/listPagination.ts";
import { fetchAllMedia } from "../frontend/src/media/fetchAllMedia.ts";

let failed = 0;

function expectThrow(fn: () => void) {
  try {
    fn();
    failed += 1;
  } catch (err) {
    if (!(err instanceof MediaListCursorError)) {
      failed += 1;
    }
  }
}

expectThrow(() => assertMediaPageCursor("12", "12", 5, 0));
expectThrow(() => assertMediaPageCursor("12", "10", 5, 0));
expectThrow(() => assertMediaPageCursor(null, "next", 0, 0));
expectThrow(() => assertMediaPageCursor("12", "abc", 5, 0));
expectThrow(() => assertMediaPageCursor("12", "13", 5, MAX_MEDIA_PAGES));

async function testFetchAllMediaStopsAtGuard() {
  let calls = 0;
  try {
    await fetchAllMedia(
      async () => {
        calls += 1;
        return { items: [{ id: calls }], next_cursor: String(calls) };
      },
      () => false,
      () => undefined,
    );
    failed += 1;
  } catch (err) {
    if (!(err instanceof MediaListCursorError)) {
      failed += 1;
    }
  }
  if (calls < 2) {
    failed += 1;
  }
}

async function testFetchAllMediaAggregates() {
  const pages = [
    { items: [{ id: 1 }], next_cursor: "10" },
    { items: [{ id: 2 }], next_cursor: null },
  ];
  let idx = 0;
  const all = await fetchAllMedia(
    async () => pages[idx++],
    () => false,
    () => undefined,
  );
  if (all.length !== 2 || all[0].id !== 1 || all[1].id !== 2) {
    failed += 1;
  }
}

await testFetchAllMediaStopsAtGuard();
await testFetchAllMediaAggregates();

if (failed) {
  process.exit(1);
}

process.stdout.write("ok\n");

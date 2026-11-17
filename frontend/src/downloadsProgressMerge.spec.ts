import { describe, expect, it } from "vitest";

import { mergeDownloadJobs, shouldResyncDownloadsFromSnapshot } from "./downloadsProgressMerge";

function job(id: number, state: string) {
  return { id, state, media_title: `item-${id}` };
}

describe("mergeDownloadJobs", () => {
  it("conserve les annulés absents de la trame SSE", () => {
    const existing = [job(1, "downloading"), job(2, "cancelled")];
    const incoming = [job(1, "downloading")];
    const merged = mergeDownloadJobs(existing, incoming);
    expect(merged).toHaveLength(2);
    expect(merged.find((row) => row.id === 2)?.state).toBe("cancelled");
  });

  it("ne vide pas la liste sur une trame vide", () => {
    const existing = [job(10, "failed"), job(11, "done")];
    const merged = mergeDownloadJobs(existing, []);
    expect(merged).toHaveLength(2);
    expect(merged.map((row) => row.id).sort()).toEqual([10, 11]);
  });

  it("trie par id décroissant après fusion", () => {
    const existing = [job(3, "done"), job(7, "cancelled")];
    const incoming = [job(12, "downloading"), job(5, "queued")];
    const merged = mergeDownloadJobs(existing, incoming);
    expect(merged.map((row) => row.id)).toEqual([12, 7, 5, 3]);
  });
});

describe("shouldResyncDownloadsFromSnapshot", () => {
  it("demande une resync sur trame vide avec un job actif en local", () => {
    const local = [job(4, "downloading"), job(2, "cancelled")];
    expect(shouldResyncDownloadsFromSnapshot(local, [])).toBe(true);
  });

  it("ne demande pas de resync sur trame vide sans job actif", () => {
    const local = [job(8, "done"), job(6, "cancelled")];
    expect(shouldResyncDownloadsFromSnapshot(local, [])).toBe(false);
  });
});

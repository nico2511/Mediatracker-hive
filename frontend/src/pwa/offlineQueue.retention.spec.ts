import { beforeEach, describe, expect, it } from "vitest";

import { setFixedRetentionForTests } from "../fixedRetention";
import { isExpired, type QueueItem } from "./offlineQueue";

const item: QueueItem = {
  id: "q-1",
  tmdb_id: 1,
  type: "series",
  added_at: "2020-01-01T00:00:00.000Z",
};

beforeEach(() => {
  setFixedRetentionForTests(null);
});

describe("offlineQueue isExpired sans fixed-retention", () => {
  it("ne lève pas et ne considère pas expiré", () => {
    expect(() => isExpired(item)).not.toThrow();
    expect(isExpired(item)).toBe(false);
  });
});
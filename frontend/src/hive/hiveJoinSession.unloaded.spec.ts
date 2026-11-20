import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { setClientConstantsForTests } from "../clientConstants";
import { clearHiveJoinWait, readHiveJoinWait, writeHiveJoinWait } from "./hiveJoinSession";

const sample = {
  ruche_id_hex: "ab".repeat(32),
  creator_id: "creator-1",
  creator_base_url: "https://mtr.exemple.fr",
  control_code: "AB12CD34",
};

beforeEach(() => {
  setClientConstantsForTests(null);
  clearHiveJoinWait();
});

afterEach(() => {
  clearHiveJoinWait();
});

describe("hiveJoinSession sans client_constants", () => {
  it("ne lève pas et ne purge pas par TTL", () => {
    const started = new Date(Date.now() - 100 * 24 * 60 * 60 * 1000).toISOString();
    localStorage.setItem(
      "mediatracker:hive-join-wait",
      JSON.stringify({ v: 2, started_at: started, ...sample }),
    );
    expect(() => readHiveJoinWait()).not.toThrow();
    expect(readHiveJoinWait()).toEqual(sample);
  });

  it("write puis read sans cache chargé", () => {
    writeHiveJoinWait(sample);
    expect(readHiveJoinWait()).toEqual(sample);
  });
});
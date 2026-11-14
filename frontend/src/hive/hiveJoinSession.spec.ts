import { afterEach, describe, expect, it } from "vitest";

import { clearHiveJoinWait, readHiveJoinWait, writeHiveJoinWait } from "./hiveJoinSession";

const sample = {
  ruche_id_hex: "ab".repeat(32),
  creator_id: "creator-1",
  creator_base_url: "https://mtr.exemple.fr",
  control_code: "AB12CD34",
};

afterEach(() => {
  clearHiveJoinWait();
});

describe("hiveJoinSession", () => {
  it("persiste et relit l’état d’attente sans lien d’invitation", () => {
    expect(readHiveJoinWait()).toBeNull();
    writeHiveJoinWait(sample);
    expect(readHiveJoinWait()).toEqual(sample);
    const raw = localStorage.getItem("mediatracker:hive-join-wait") || "";
    expect(raw).not.toContain("mtrhive");
    expect(raw).not.toContain('"link"');
    clearHiveJoinWait();
    expect(readHiveJoinWait()).toBeNull();
  });

  it("purge l’ancien format avec lien d’invitation", () => {
    localStorage.setItem(
      "mediatracker:hive-join-wait",
      JSON.stringify({
        v: 2,
        link: "mtrhive:1:secret",
        ...sample,
        started_at: new Date().toISOString(),
      }),
    );
    expect(readHiveJoinWait()).toBeNull();
    expect(localStorage.getItem("mediatracker:hive-join-wait")).toBeNull();
  });

  it("purge une version de stockage inconnue", () => {
    localStorage.setItem(
      "mediatracker:hive-join-wait",
      JSON.stringify({ v: 1, link: "mtrhive:1:x", control_code: "AA" }),
    );
    expect(readHiveJoinWait()).toBeNull();
  });
});

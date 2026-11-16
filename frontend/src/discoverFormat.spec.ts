import { describe, expect, it } from "vitest";

import { discoverDateLabel, discoverInfoLine } from "./discoverFormat";

describe("discoverFormat", () => {
  it("affiche la date complète en français", () => {
    const label = discoverDateLabel({ release_date: "2026-05-01", year: 2026 });
    expect(label).toContain("2026");
    expect(label).toContain("mai");
  });

  it("retombe sur l’année", () => {
    expect(discoverDateLabel({ release_date: null, year: 2026 })).toBe("2026");
  });

  it("compose la ligne type · date", () => {
    const line = discoverInfoLine({ media_type: "film", release_date: "2026-05-01", year: 2026 });
    expect(line.startsWith("Film ·")).toBe(true);
  });
});
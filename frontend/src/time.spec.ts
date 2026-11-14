import { describe, expect, it } from "vitest";

import { formatParisClock, parseInstant } from "./time";

describe("parseInstant", () => {
  it("accepte un Date sans appeler trim", () => {
    const d = new Date("2026-11-09T20:00:00.000Z");
    expect(parseInstant(d)?.toISOString()).toBe(d.toISOString());
    expect(formatParisClock(d)).toMatch(/\d{2}:\d{2}/);
  });

  it("ne plante pas quand formatParisClock reçoit une chaîne ISO", () => {
    expect(formatParisClock("2026-11-09T20:00:00.000Z")).toMatch(/\d{2}:\d{2}/);
  });
});

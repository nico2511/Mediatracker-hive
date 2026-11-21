import { describe, expect, it } from "vitest";

import {
  hiveGossipCountdownLabel,
  hiveGossipProgressFraction,
  parseIsoUtc,
} from "./hiveGossipSync";

describe("hiveGossipSync", () => {
  it("formate le compte à rebours", () => {
    expect(hiveGossipCountdownLabel(30_000)).toBe("imminente");
    expect(hiveGossipCountdownLabel(90_000)).toBe("dans 2 min");
    expect(hiveGossipCountdownLabel(240_000)).toBe("dans 4 min");
  });

  it("calcule la progression entre dernier et prochain passage", () => {
    const last = Date.parse("2025-06-01T12:00:00Z");
    const next = Date.parse("2025-06-01T12:10:00Z");
    const mid = last + 6 * 60_000;
    expect(hiveGossipProgressFraction(mid, next, last)).toBeCloseTo(0.6, 5);
    expect(hiveGossipProgressFraction(next, next, last)).toBe(1);
  });

  it("parse les ISO UTC", () => {
    expect(parseIsoUtc("2025-06-01T12:10:00Z")).toBe(Date.parse("2025-06-01T12:10:00Z"));
    expect(parseIsoUtc(null)).toBeNull();
  });

  it("affiche imminente si l’échéance est déjà atteinte", () => {
    expect(hiveGossipCountdownLabel(0)).toBe("imminente");
    expect(hiveGossipCountdownLabel(-5_000)).toBe("imminente");
  });

  it("reste à 100 % si next est passé, et à 0 si next est nul", () => {
    const last = Date.parse("2025-06-01T12:00:00Z");
    const next = Date.parse("2025-06-01T12:10:00Z");
    expect(hiveGossipProgressFraction(next + 1_000, next, last)).toBe(1);
    expect(hiveGossipProgressFraction(next, null, last)).toBe(0);
  });

  it("ignore un ISO invalide", () => {
    expect(parseIsoUtc("")).toBeNull();
    expect(parseIsoUtc("pas-une-date")).toBeNull();
  });
});

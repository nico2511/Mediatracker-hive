import { describe, expect, it } from "vitest";

import {
  buildEffectiveProfileChain,
  describeEffectiveChain,
  formatProfileFallbackOrder,
  parseProfileFallbackOrder,
  qualityProfileSummary,
  type QualityProfileLike,
} from "./profileFallback";

const compact: QualityProfileLike = {
  id: 1,
  name: "Compact",
  languages: ["VF"],
  min_resolution: "HD",
  max_resolution: "HD",
  min_size_mb: null,
  max_size_mb: 2048,
};

const souple: QualityProfileLike = {
  id: 2,
  name: "Souple",
  languages: ["VF", "MULTI"],
  min_resolution: "HD",
  max_resolution: "1080p",
  min_size_mb: null,
  max_size_mb: 6144,
};

describe("profileFallback order", () => {
  it("parse vide et déduplique", () => {
    expect(parseProfileFallbackOrder("")).toEqual([]);
    expect(parseProfileFallbackOrder("2, 1, 2, 3")).toEqual([2, 1, 3]);
    expect(formatProfileFallbackOrder([7, 3, 12])).toBe("7,3,12");
  });

  it("chaîne effective avec repli et déduplication du principal", () => {
    expect(buildEffectiveProfileChain(1, [1, 2], true)).toEqual([1, 2]);
    expect(buildEffectiveProfileChain(1, [2, 3], true)).toEqual([1, 2, 3]);
    expect(buildEffectiveProfileChain(1, [2], false)).toEqual([1]);
    expect(buildEffectiveProfileChain(1, [], true)).toEqual([1]);
  });

  it("résume un profil et la chaîne affichée", () => {
    expect(qualityProfileSummary(compact)).toContain("VF");
    expect(qualityProfileSummary(souple)).toContain("1080p");
    const byId = new Map([
      [1, compact],
      [2, souple],
    ]);
    const chain = buildEffectiveProfileChain(1, [1, 2], true);
    const { rows, hint } = describeEffectiveChain(chain, byId, 1, [1, 2]);
    expect(rows.map((r) => r.role)).toEqual(["principal", "repli global"]);
    expect(hint).toContain("n'est pas répété");
  });
});

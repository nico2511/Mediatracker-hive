import { describe, expect, it } from "vitest";

import { mergeCommaOrderPreservingHidden, settingCommaTokens } from "./settingsCoerce";

describe("mergeCommaOrderPreservingHidden", () => {
  it("ignore un id absent du modelValue (comportement SourceRank)", () => {
    const merged = mergeCommaOrderPreservingHidden("comet", ["comet", "hive"], ["comet", "hive"]);
    expect(merged).toBe("comet");
  });

  it("conserve les jetons hors visibleIds", () => {
    const merged = mergeCommaOrderPreservingHidden(
      "wawacity,hive,comet",
      ["wawacity", "comet"],
      ["comet", "wawacity"],
    );
    expect(merged).toBe("comet,hive,wawacity");
    expect(settingCommaTokens(merged)).toEqual(["comet", "hive", "wawacity"]);
  });
});
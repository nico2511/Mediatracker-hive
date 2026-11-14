import { describe, expect, it } from "vitest";

import { settingAsCommaList, settingAsLineList, settingCommaTokens } from "./settingsCoerce";

describe("settingsCoerce", () => {
  it("convertit un tableau Comet en lignes", () => {
    expect(settingAsLineList(["https://a.example", 42])).toEqual(["https://a.example", "42"]);
  });

  it("convertit un tableau d’ordre de sources", () => {
    expect(settingCommaTokens(["Wawacity", "comet"])).toEqual(["wawacity", "comet"]);
    expect(settingAsCommaList(["hive", "torrentio"])).toBe("hive,torrentio");
  });
});

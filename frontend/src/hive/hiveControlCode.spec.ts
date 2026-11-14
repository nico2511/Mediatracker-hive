import { describe, expect, it } from "vitest";

import { hiveControlCodesMatch, normalizeHiveControlCode } from "./hiveControlCode";

describe("hiveControlCode", () => {
  it("normalise sans espaces et en majuscules", () => {
    expect(normalizeHiveControlCode(" ab2cde34 ")).toBe("AB2CDE34");
  });

  it("refuse une correspondance partielle ou erronée", () => {
    expect(hiveControlCodesMatch("AB12CD34", "AB12CD34")).toBe(true);
    expect(hiveControlCodesMatch("ab12cd34", "AB12CD34")).toBe(true);
    expect(hiveControlCodesMatch("WRONGCOD", "AB12CD34")).toBe(false);
    expect(hiveControlCodesMatch("AB12", "AB12CD34")).toBe(false);
  });
});

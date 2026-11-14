import { describe, expect, it } from "vitest";

import { hiveRucheLabel } from "./hiveRucheLabel";

describe("hiveRucheLabel", () => {
  it("affiche hex court, effectif et créateur", () => {
    expect(
      hiveRucheLabel({
        ruche_id_hex: "144e873b32b218d353513faf271006de",
        member_count: 2,
        is_creator: true,
      }),
    ).toBe("Ruche 144e873b… · 2 membres (créateur)");
  });

  it("gère un seul membre", () => {
    expect(
      hiveRucheLabel({ ruche_id_hex: "ab" + "0".repeat(30), member_count: 1, is_creator: false }),
    ).toBe("Ruche ab000000… · 1 membre");
  });
});

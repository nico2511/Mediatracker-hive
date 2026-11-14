import { describe, expect, it } from "vitest";

import { empreintePhaseClass, empreintePhaseLabel } from "./hiveEmpreintes";

describe("hiveEmpreintes", () => {
  it("phase labels", () => {
    expect(empreintePhaseLabel("pret")).toBe("Prêt");
    expect(empreintePhaseClass("ecart")).toBe("danger");
    expect(empreintePhaseClass("a_controler")).toBe("info");
  });
});

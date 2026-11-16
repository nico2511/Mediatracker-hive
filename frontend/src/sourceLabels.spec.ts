import { describe, expect, it } from "vitest";

import { sourceDisplayLabel } from "./sourceLabels";

describe("sourceDisplayLabel", () => {
  it("évite les identifiants techniques", () => {
    expect(sourceDisplayLabel("stremio_addons")).toBe("Addons Stremio");
    expect(sourceDisplayLabel("free_telecharger")).toBe("Free-Télécharger");
  });

  it("préfère le libellé API s’il est lisible", () => {
    expect(sourceDisplayLabel("comet", "Comet")).toBe("Comet");
  });
});
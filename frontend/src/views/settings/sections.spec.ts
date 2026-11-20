import { describe, expect, it } from "vitest";

import { sectionBySlug, SETTINGS_GROUPS, SETTINGS_SECTIONS } from "./sections";

describe("settings sections — titres", () => {
  it("nomme la page jetons « API et jetons » sans changer le slug", () => {
    const jetons = sectionBySlug("jetons");
    expect(jetons?.slug).toBe("jetons");
    expect(jetons?.title).toBe("API et jetons");
    expect(SETTINGS_SECTIONS.find((row) => row.slug === "jetons")?.title).toBe("API et jetons");
  });

  it("n'affiche plus de groupe « Services et jetons » en doublon de la page", () => {
    expect(SETTINGS_GROUPS.some((group) => group.title === "Services et jetons")).toBe(false);
    expect(sectionBySlug("jetons")?.group).toBe("general");
  });
});

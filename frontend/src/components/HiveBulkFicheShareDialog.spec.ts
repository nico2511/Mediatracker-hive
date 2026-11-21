import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import HiveBulkFicheShareDialog from "./HiveBulkFicheShareDialog.vue";

describe("HiveBulkFicheShareDialog", () => {
  it("affiche la confirmation et le rappel séries", () => {
    const wrapper = mount(HiveBulkFicheShareDialog, {
      props: {
        open: true,
        count: 2,
        titles: ["Fiche A (2090)", "Fiche B (2091)"],
      },
    });
    expect(wrapper.text()).toContain("Partager sur Hive ?");
    expect(wrapper.text()).toContain("Fiche A (2090)");
    expect(wrapper.text()).toContain("épisodes présents sur disque");
  });
});
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import FicheHideMenuEntry from "./FicheHideMenuEntry.vue";

describe("FicheHideMenuEntry", () => {
  it("affiche le libellé et la phrase d'aide", () => {
    const wrapper = mount(FicheHideMenuEntry);
    expect(wrapper.text()).toContain("Masquer la fiche");
    expect(wrapper.text()).toContain("Rien n'est supprimé");
    void wrapper.find("button").trigger("click");
    expect(wrapper.emitted("hide")).toHaveLength(1);
  });
});
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DeleteFicheDialog from "./DeleteFicheDialog.vue";

describe("DeleteFicheDialog", () => {
  it("affiche les titres tronqués et l’avertissement sans corbeille en mode fichiers intacts", async () => {
    const titles = ["Alpha fictif", "Bravo fictif", "Charlie fictif", "Delta fictif", "Echo fictif"];
    const wrapper = mount(DeleteFicheDialog, {
      props: {
        open: true,
        count: titles.length,
        titles,
        titlePreviewMax: 3,
        filesOnDiskUntouched: true,
      },
    });
    expect(wrapper.text()).toContain("Supprimer 5 fiches ?");
    expect(wrapper.text()).toContain("Alpha fictif");
    expect(wrapper.text()).toContain("et 2 autres");
    expect(wrapper.text()).toContain("Les fichiers sur le disque ne sont pas touchés.");
    expect(wrapper.text()).not.toContain("Et les fichiers ?");

    await wrapper.find("form.dialog").trigger("submit.prevent");
    expect(wrapper.emitted("confirm")).toEqual([[false]]);
  });
});
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import StremioAddonList from "./StremioAddonList.vue";

describe("StremioAddonList", () => {
  it("réordonne et active ou désactive une ligne", async () => {
    const wrapper = mount(StremioAddonList, {
      props: {
        modelValue: [
          { url: "https://alpha.fictif.example", enabled: true },
          { url: "https://beta.fictif.example", enabled: false },
        ],
      },
    });
    expect(wrapper.find("code").attributes("title")).toBe("https://alpha.fictif.example");
    const down = wrapper
      .findAll("button")
      .find((btn) => btn.attributes("aria-label") === "Descendre" && !btn.attributes("disabled"));
    await down?.trigger("click");
    const emits = wrapper.emitted("update:modelValue");
    expect(emits?.at(-1)?.[0]).toEqual([
      { url: "https://beta.fictif.example", enabled: false },
      { url: "https://alpha.fictif.example", enabled: true },
    ]);
    const toggle = wrapper.find('input[type="checkbox"]');
    await toggle.setValue(false);
    expect(wrapper.emitted("update:modelValue")?.at(-1)?.[0]).toEqual([
      { url: "https://alpha.fictif.example", enabled: false },
      { url: "https://beta.fictif.example", enabled: false },
    ]);
  });

  it("ajoute une URL pleine largeur sans champ libellé", async () => {
    const wrapper = mount(StremioAddonList, {
      props: { modelValue: [] },
    });
    expect(wrapper.text()).not.toContain("Libellé");
    const input = wrapper.find(".comet-instance-add input");
    await input.setValue("https://nouveau.fictif.example");
    await wrapper.findAll("button").find((btn) => btn.text() === "Ajouter")?.trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]?.[0]).toEqual([
      { url: "https://nouveau.fictif.example", enabled: true },
    ]);
  });
});
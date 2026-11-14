import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import CometInstanceList from "./CometInstanceList.vue";

describe("CometInstanceList", () => {
  it("affiche la mention pour la première instance et permet de réordonner", async () => {
    const wrapper = mount(CometInstanceList, {
      props: {
        modelValue: ["https://un.example", "https://deux.example"],
      },
    });
    expect(wrapper.text()).toContain("utilisée en premier");
    expect(wrapper.text()).toContain("https://un.example");
    const down = wrapper
      .findAll("button")
      .find((btn) => btn.attributes("aria-label") === "Descendre" && !btn.attributes("disabled"));
    await down?.trigger("click");
    const emits = wrapper.emitted("update:modelValue");
    expect(emits?.at(-1)?.[0]).toEqual(["https://deux.example", "https://un.example"]);
  });

  it("ajoute une adresse sans doublon", async () => {
    const wrapper = mount(CometInstanceList, {
      props: { modelValue: ["https://a.example"] },
    });
    await wrapper.find("input").setValue("https://a.example/");
    await wrapper.find("button").trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    await wrapper.find("input").setValue("HTTPS://A.EXAMPLE");
    await wrapper.findAll("button").find((btn) => btn.text() === "Ajouter")?.trigger("click");
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    await wrapper.find("input").setValue("https://b.example");
    await wrapper.findAll("button").find((btn) => btn.text() === "Ajouter")?.trigger("click");
    expect(wrapper.emitted("update:modelValue")?.[0]?.[0]).toEqual(["https://a.example", "https://b.example"]);
  });
});

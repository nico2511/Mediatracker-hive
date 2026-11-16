import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import SourceRank from "./SourceRank.vue";

describe("SourceRank", () => {
  it("tolère un modelValue tableau", async () => {
    const wrapper = mount(SourceRank, {
      props: {
        modelValue: ["wawacity", "comet"] as unknown as string,
        orderOnly: true,
      },
    });
    expect(wrapper.text()).toContain("Wawacity");
    expect(wrapper.text()).toContain("Comet");
  });

  it("visibleIds limite la liste affichée", async () => {
    const wrapper = mount(SourceRank, {
      props: {
        modelValue: "wawacity,comet,hive",
        visibleIds: ["comet"],
        orderOnly: true,
      },
    });
    expect(wrapper.text()).toContain("Comet");
    expect(wrapper.text()).not.toContain("Wawacity");
    expect(wrapper.text()).not.toContain("Hive");
  });

  it("visibleIds vide n’affiche aucune source", async () => {
    const wrapper = mount(SourceRank, {
      props: {
        modelValue: "wawacity,comet,hive",
        visibleIds: [],
        orderOnly: true,
      },
    });
    expect(wrapper.text()).not.toContain("Comet");
    expect(wrapper.text()).not.toContain("Wawacity");
    expect(wrapper.text()).not.toContain("Hive");
  });

  it("orderOnly conserve les sources activées en réordonnant", async () => {
    const wrapper = mount(SourceRank, {
      props: {
        modelValue: "wawacity,comet",
        orderOnly: true,
      },
    });
    const down = wrapper.findAll("button").find((btn) => btn.text() === "↓");
    expect(down).toBeTruthy();
    await down!.trigger("click");
    const updates = wrapper.emitted("update:modelValue") || [];
    const last = updates.at(-1)?.[0] as string;
    expect(last.split(",").sort()).toEqual(["comet", "wawacity"]);
  });

  it("place les sources désactivées en fin avec la mention désactivée", () => {
    const wrapper = mount(SourceRank, {
      props: {
        modelValue: "wawacity,comet",
        orderOnly: true,
        visibleIds: ["wawacity", "comet", "free_telecharger"],
      },
    });
    const items = wrapper.findAll(".rank-list li");
    expect(items.at(-1)?.classes()).toContain("is-off");
    expect(wrapper.text()).toContain("désactivée");
    expect(wrapper.text()).toContain("Free-Télécharger");
    expect(wrapper.text()).not.toContain("free_telecharger");
  });

  it("orderOnly garde la position des jetons hors visibleIds", async () => {
    const wrapper = mount(SourceRank, {
      props: {
        modelValue: "wawacity,hive,comet",
        visibleIds: ["wawacity", "comet"],
        orderOnly: true,
      },
    });
    const down = wrapper.findAll("button").find((btn) => btn.text() === "↓");
    expect(down).toBeTruthy();
    await down!.trigger("click");
    const updates = wrapper.emitted("update:modelValue") || [];
    const last = updates.at(-1)?.[0] as string;
    expect(last).toBe("comet,hive,wawacity");
  });
});

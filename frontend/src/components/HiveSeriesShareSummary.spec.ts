import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import HiveSeriesShareSummary from "./HiveSeriesShareSummary.vue";

vi.mock("../api", () => ({
  api: vi.fn().mockResolvedValue([]),
}));

vi.mock("./ActionMenuPopover.vue", () => ({
  default: {
    name: "ActionMenuPopover",
    template: "<div><slot /></div>",
  },
}));

vi.mock("./HiveBulkShareMenuItems.vue", () => ({
  default: {
    name: "HiveBulkShareMenuItems",
    template: "<div />",
  },
}));

import type { HiveShareUiState } from "../hive/hiveBadges";

const baseState: HiveShareUiState = {
  shareable: true,
  shared: false,
  exclude_all: false,
  excluded_ruche_ids: [],
  preparing: false,
  transfer_active: false,
  transfer_progress_pct: null,
  hive_keep: false,
  keep: false,
  pull_only: false,
  has_file: true,
  catalog_entry_local: false,
};

const episodeStates: Record<number, HiveShareUiState> = { 1: baseState };

describe("HiveSeriesShareSummary", () => {
  it("applique les attributs du parent sur la racine layout card", async () => {
    const wrapper = mount(HiveSeriesShareSummary, {
      props: {
        hiveEnabled: true,
        episodeStates,
        mediaId: 1,
        layout: "card",
      },
      attrs: { class: "fiche-layout-b" },
    });
    await flushPromises();
    const root = wrapper.get("[data-testid='hive-series-summary']");
    expect(root.classes()).toContain("fiche-layout-b");
    expect(root.classes()).toContain("fiche-hive-card");
  });

  it("applique les attributs du parent sur la racine layout inline", () => {
    const wrapper = mount(HiveSeriesShareSummary, {
      props: {
        hiveEnabled: true,
        episodeStates,
        mediaId: 1,
        layout: "inline",
      },
      attrs: { class: "fiche-layout-b" },
    });
    const root = wrapper.get("[data-testid='hive-series-summary']");
    expect(root.classes()).toContain("fiche-layout-b");
    expect(root.classes()).toContain("hive-series-summary");
  });
});
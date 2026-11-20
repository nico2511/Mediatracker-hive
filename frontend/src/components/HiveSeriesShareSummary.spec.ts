import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("./ActionMenuPopover.vue", () => ({
  default: {
    name: "ActionMenuPopover",
    props: ["label"],
    template: "<div class='action-menu'><span>{{ label }}</span><slot /></div>",
  },
}));

vi.mock("./HiveBulkShareMenuItems.vue", () => ({
  default: {
    name: "HiveBulkShareMenuItems",
    template: "<div />",
  },
}));

vi.mock("./HiveEmpreintesPanel.vue", () => ({
  default: {
    name: "HiveEmpreintesPanel",
    template: "<div />",
  },
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import type { HiveShareUiState } from "../hive/hiveBadges";
import type { HiveMyRequestItem } from "../hive/hiveRequests";

import HiveSeriesShareSummary from "./HiveSeriesShareSummary.vue";

const baseState: HiveShareUiState = {
  shareable: true,
  shared: true,
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

const episodeStates: Record<number, HiveShareUiState> = {
  1: { ...baseState, shared: true },
  2: { ...baseState, shared: false },
};

const mediaRequests: HiveMyRequestItem[] = [
  {
    media_id: 1,
    media_item_id: 10,
    work_key: "wk-1",
    title: "Série Exemple A",
    type: "series",
    season: 1,
    episode: 1,
    status: "pending",
    hive_request_available: false,
  },
  {
    media_id: 1,
    media_item_id: 11,
    work_key: "wk-2",
    title: "Série Exemple A",
    type: "series",
    season: 1,
    episode: 2,
    status: "transfer",
    hive_request_available: false,
  },
];

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue([]);
});

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
    const root = wrapper.get("[data-testid='hive-fiche-card']");
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

  it("regroupe partage et demande dans un seul bloc carte", async () => {
    const wrapper = mount(HiveSeriesShareSummary, {
      props: {
        hiveEnabled: true,
        episodeStates,
        mediaId: 1,
        layout: "card",
        mediaRequests,
      },
    });
    await flushPromises();
    const card = wrapper.get("[data-testid='hive-fiche-card']");
    expect(card.find("[data-testid='hive-fiche-partage']").exists()).toBe(true);
    expect(card.find("[data-testid='hive-fiche-demande']").exists()).toBe(true);
    expect(wrapper.findAll("[data-testid='hive-fiche-card']").length).toBe(1);
    expect(card.text()).not.toContain("Demandé à la ruche");
    expect(card.get("[data-testid='hive-fiche-partage-line']").text()).toContain("1/2");
    expect(card.get("[data-testid='hive-fiche-demande-line']").text()).toContain(
      "2 épisodes demandés · 1 en transfert",
    );
    expect(card.get("[data-testid='hive-fiche-request-open-mine']").exists()).toBe(true);
    expect(card.get("[data-testid='hive-fiche-request-retract-all']").exists()).toBe(true);
    const gerer = card.get(".fiche-hive-partage-gerer");
    expect(gerer.text()).toContain("Gérer");
  });

  it("masque la section demande sans suivi", async () => {
    const wrapper = mount(HiveSeriesShareSummary, {
      props: {
        hiveEnabled: true,
        episodeStates,
        mediaId: 1,
        layout: "card",
        mediaRequests: [],
      },
    });
    await flushPromises();
    expect(wrapper.find("[data-testid='hive-fiche-demande']").exists()).toBe(false);
  });
});
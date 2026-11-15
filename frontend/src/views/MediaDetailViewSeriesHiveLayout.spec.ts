import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";

import serieNonSuivie from "../revue/v3_serie_non_suivie.json";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {
    status?: number;
    constructor(message: string, status?: number) {
      super(message);
      this.status = status;
    }
  },
  userFacingApiError: (_e: unknown, f: string) => f,
}));
vi.mock("../settings", () => ({ fetchSettings: async () => ({ settings: [] }) }));
vi.mock("../composables/useProgressStream", () => ({ useProgressStream: () => undefined }));
vi.mock("../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: ref(true) }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: String(serieNonSuivie.media.id) }, query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import MediaDetailView from "./MediaDetailView.vue";

function stubSeriesApi() {
  const media = { ...serieNonSuivie.media };
  apiMock.mockImplementation(async (url: string) => {
    const id = media.id;
    if (url === `/api/v1/media/${id}`) {
      return media;
    }
    if (url === `/api/v1/media/${id}/items`) {
      return serieNonSuivie.items;
    }
    if (url.startsWith("/api/v1/quality-profiles")) {
      return { profiles: [] };
    }
    if (url.startsWith(`/api/v1/media/${id}/identity`)) {
      return [];
    }
    if (url.startsWith(`/api/v1/media/${id}/library-folders`)) {
      return { items: [] };
    }
    if (url.startsWith(`/api/v1/media/${id}/jellyfin`)) {
      throw new Error("jellyfin off");
    }
    if (url.startsWith("/api/v1/hive/items/sharing")) {
      return [];
    }
    if (/^\/api\/v1\/hive\/items\/\d+\/sharing$/.test(url)) {
      return {
        shareable: false,
        shared: false,
        exclude_all: false,
        excluded_ruche_ids: [],
        preparing: false,
        transfer_active: false,
        transfer_progress_pct: null,
        hive_keep: false,
        keep: false,
        pull_only: false,
        has_file: false,
        catalog_entry_local: false,
      };
    }
    if (url === "/api/v1/hive/requests/mine") {
      return { items: [] };
    }
    throw new Error(`non simulé ${url}`);
  });
}

describe("Fiche série — emplacements Hive layout A/B", () => {
  it("place le résumé Hive mobile dans un conteneur .fiche-layout-b et le desktop dans .fiche-layout-a", async () => {
    stubSeriesApi();
    const wrapper = mount(MediaDetailView, {
      global: {
        stubs: { Teleport: true, RouterLink: true, TmdbPosterImg: true, StatusIcon: true, SourceRank: true },
        attachTo: document.body,
      },
    });
    await flushPromises();

    const summaries = wrapper.findAll("[data-testid='hive-series-summary']");
    expect(summaries.length).toBe(2);

    const mobileSummary = summaries[0]!;
    expect(mobileSummary.classes()).not.toContain("fiche-layout-b");
    expect(mobileSummary.element.parentElement?.classList.contains("fiche-layout-b")).toBe(true);

    const desktopBlock = wrapper.get(".fiche-series-desktop.fiche-layout-a");
    expect(desktopBlock.find("[data-testid='hive-series-summary']").exists()).toBe(true);
  });
});
import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";

import filmNonSuivi from "../revue/v1_film_non_suivi.json";

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
  useRoute: () => ({ params: { id: String(filmNonSuivi.media.id) }, query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import MediaDetailView from "./MediaDetailView.vue";

function stubFilmApi() {
  const media = { ...filmNonSuivi.media, followed: false };
  apiMock.mockImplementation(async (url: string) => {
    const id = media.id;
    if (url === `/api/v1/media/${id}`) {
      return media;
    }
    if (url === `/api/v1/media/${id}/items`) {
      return filmNonSuivi.items;
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
    if (url === "/api/v1/hive/requests/mine") {
      return { items: [] };
    }
    if (url === "/api/v1/hive/ruches") {
      return [];
    }
    if (/^\/api\/v1\/hive\/items\/\d+\/sharing$/.test(url)) {
      return {
        shareable: true,
        shared: false,
        exclude_all: true,
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
    }
    throw new Error(`non simulé ${url}`);
  });
}

describe("Fiche non suivie et bloc Hive", () => {
  it("masque Suivi et Réglages, affiche Suivre à nouveau et Plus d'actions", async () => {
    stubFilmApi();
    const wrapper = mount(MediaDetailView, {
      global: {
        stubs: { Teleport: true, RouterLink: true, TmdbPosterImg: true, StatusIcon: true, SourceRank: true },
        attachTo: document.body,
      },
    });
    await flushPromises();

    expect(wrapper.find(".fiche-fold--suivi").exists()).toBe(false);
    expect(wrapper.find("[data-fiche-settings]").exists()).toBe(false);
    expect(wrapper.find("[data-testid='fiche-follow-again']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='fiche-unfollowed-more']").exists()).toBe(true);
    expect(wrapper.find(".film-file-panel").exists()).toBe(true);
  });

  it("ne rend qu'un bloc Hive avec empreintes replié par défaut", async () => {
    stubFilmApi();
    const wrapper = mount(MediaDetailView, {
      global: {
        stubs: { Teleport: true, RouterLink: true, TmdbPosterImg: true, StatusIcon: true, SourceRank: true },
        attachTo: document.body,
      },
    });
    await flushPromises();

    expect(wrapper.findAll("[data-testid='hive-fiche-card']").length).toBe(1);
    expect(wrapper.find("[data-testid='hive-empreintes-standalone']").exists()).toBe(false);
    const fold = wrapper.get("[data-testid='hive-empreintes-fold']").element as HTMLDetailsElement;
    expect(fold.open).toBe(false);
  });
});
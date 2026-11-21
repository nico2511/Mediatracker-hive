import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({ api: apiMock, userFacingApiError: (_e: unknown, f: string) => f }));
vi.mock("../settings", () => ({
  fetchSettings: async () => ({
    settings: [{ key: "tmdb_region", value: "FR", secret: false, set: true }],
  }),
}));
vi.mock("../composables/useProgressStream", () => ({ useProgressStream: () => undefined }));
vi.mock("../composables/useHive", () => ({ useHiveEnabled: () => ({ hiveEnabled: { value: false } }) }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "42" }, query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import MediaDetailView from "./MediaDetailView.vue";

const media = {
  id: 42,
  type: "series",
  title: "Série exemple 3",
  year: 2017,
  overview: null,
  poster_url: null,
  backdrop_url: null,
  tmdb_id: 900003,
  tmdb_url: null,
  series_status: "Returning Series",
  followed: true,
  on_hold: false,
  auto_download: true,
  new_episode_policy: "auto",
  auto_status: "idle",
  pref_quality: "HD",
  pref_language: "VFF",
  allow_fallback: false,
  fallback_profile_ids: null,
  source_order: null,
  quality_profile_id: null,
  scan_interval_min: null,
  keep: false,
  auto_delete: false,
  auto_delete_days: null,
  poll: {
    allowed: false,
    reason: "",
    last_check_at: null,
    next_check_at: null,
    last_result: null,
    last_message: null,
    interval_min: 720,
  },
  next_episode: null,
  progress: { watched: 12, total: 26 },
  available_not_downloaded: 0,
  file_status: "absent",
  tmdb_vote_average: 8.1,
  tmdb_vote_count: 1240,
};

describe("MediaDetailView note TMDB", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/media/42") {
        return media;
      }
      if (url === "/api/v1/media/42/items") {
        return { seasons: [] };
      }
      if (url.startsWith("/api/v1/quality-profiles")) {
        return { profiles: [] };
      }
      if (url.startsWith("/api/v1/media/42/identity")) {
        return [];
      }
      throw new Error(`non simulé ${url}`);
    });
  });

  it("affiche la ligne TMDB sous le type et l’année", async () => {
    const wrapper = mount(MediaDetailView, {
      global: {
        stubs: {
          Teleport: true,
          RouterLink: true,
          TmdbPosterImg: true,
          StatusIcon: true,
          FicheStateActions: true,
          LineMoreActions: true,
        },
      },
      attachTo: document.body,
    });
    await flushPromises();
    const row = wrapper.find(".fiche-hero-tmdb-rating");
    expect(row.exists()).toBe(true);
    expect(row.text()).toContain("TMDB 8,1/10");
    expect(row.text()).toMatch(/1.240 votes/);
    wrapper.unmount();
  });
});
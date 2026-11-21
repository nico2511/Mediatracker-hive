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
  type: "film",
  title: "Exemple fiche 8",
  year: 2026,
  overview: "Résumé fictif.",
  poster_url: null,
  backdrop_url: null,
  tmdb_id: 900008,
  tmdb_url: null,
  series_status: null,
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
    reason: "before_air_date",
    last_check_at: null,
    next_check_at: null,
    last_result: null,
    last_message: null,
    interval_min: 720,
  },
  next_episode: null,
  progress: { watched: 0, total: 1 },
  available_not_downloaded: 0,
  file_status: "absent",
  film_not_yet_available: true,
  film_status_line: "Pas encore disponible, sortie numérique le 20/01/2027",
  film_availability_source: "digital_us",
  next_air_date: "2027-01-20",
  tmdb_release_by_region: {
    US: {
      theatrical: "2026-10-21",
      digital: "2027-01-20",
      physical: "2027-02-10",
    },
    FR: {
      theatrical: "2026-11-04",
      digital: "2027-03-03",
      physical: null,
    },
  },
};

describe("MediaDetailView — dates par région", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/media/42") {
        return media;
      }
      if (url === "/api/v1/media/42/items") {
        return {
          seasons: [
            {
              number: null,
              name: null,
              air_date: "2026-11-04",
              episode_count: 1,
              keep: false,
              episodes: [
                {
                  id: 1,
                  season: null,
                  episode: null,
                  state: "pending",
                  title: "Exemple fiche 8",
                  air_date: "2026-11-04",
                  downloaded: false,
                  watched: false,
                  source_available: false,
                  download_active: false,
                  keep: false,
                },
              ],
            },
          ],
        };
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

  it("affiche En salles, Numérique, Physique et la ligne de recherche", async () => {
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

    const block = wrapper.find("[data-testid='film-release-dates']");
    expect(block.exists()).toBe(true);
    const text = block.text();
    expect(text).toContain("En salles");
    expect(text).toContain("Numérique");
    expect(text).toContain("Physique");
    expect(text).toContain("États-Unis");
    expect(text).toContain("France");
    expect(text).toContain("Recherche des liens à partir du");
    expect(text).toContain("première sortie numérique (États-Unis)");
    expect(block.find(".film-release-trigger-date").exists()).toBe(true);
    expect(block.find(".film-release-line.film-release-trigger").exists()).toBe(false);
    wrapper.unmount();
  });
});
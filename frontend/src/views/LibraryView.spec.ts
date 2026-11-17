import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests } from "../composables/useServiceStatus";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const jellyfinConfiguredForTests = vi.hoisted(() => ({ value: true }));

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));
vi.mock("../settings", () => ({
  fetchSettings: () => fetchSettingsMock(),
}));

vi.mock("../components/MediaQuickView.vue", () => ({ default: { template: "<div />" } }));
vi.mock("../components/StatusIcon.vue", () => ({ default: { template: "<span />" } }));
vi.mock("../components/TmdbPosterImg.vue", () => ({ default: { template: "<span />" } }));
const routerReplace = vi.hoisted(() => vi.fn());

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: routerReplace }),
}));

import LibraryView from "./LibraryView.vue";

function serviceStatusResponse(url: string) {
  if (url === "/api/v1/integrations/alldebrid/account") {
    return { connected: true };
  }
  if (url === "/api/v1/settings/sources/status") {
    return { sources: [], download_ready: true };
  }
  if (url === "/api/v1/ha/update") {
    return { watchtower_configured: true };
  }
  return null;
}

function reviewSeries(overrides: Record<string, unknown>) {
  return {
    key: "series-calm",
    title: "Entrée Série A",
    year: 2001,
    tmdb_id: 900101,
    poster_url: null,
    media_type: "series",
    media_id: 101,
    followed: true,
    problem: null,
    duplicate: false,
    merge_hint: null,
    folders: [
      {
        path: "/library/series-a",
        name: "Series A",
        title: "Entrée Série A",
        year: 2001,
        media_type: "series",
        tmdb_id: 900101,
        file_count: 1,
        canonical: true,
        seasons: [],
      },
    ],
    merges: [],
    files: [],
    ...overrides,
  };
}

const emptyRegroup = {
  groups: [],
  unmatched: [],
  ambiguous: [],
  empty: [],
  loose: [],
  rename_id: null,
};

const emptyAlign = {
  scanning: false,
  progress: null,
  message: "Prêt",
  auto_rename: false,
  refreshed: 0,
  attaches: [],
  reidentifies: [],
  merges: [],
  orphans: [],
  cleaned_after_watch: [],
  holds: [],
  renames: [],
  pushes: [],
};

const emptyIncomplete = {
  files: [],
  moved: 0,
  folder_written_bytes: 0,
  folder_reserved_bytes: 0,
  total_written_bytes: 0,
};

const oneIncompleteFile = {
  path: "/data/incomplete/demo.mkv",
  name: "demo.mkv",
  size: 1000,
  reason: "orphelin",
  written_bytes: 1000,
  reserved_bytes: 0,
  job_id: null,
  job_state: "",
  orphan: true,
};

describe("LibraryView — revue films / séries", () => {
  beforeEach(() => {
    apiMock.mockReset();
    fetchSettingsMock.mockReset();
    resetServiceStatusForTests();
    jellyfinConfiguredForTests.value = true;
    fetchSettingsMock.mockResolvedValue({
      settings: jellyfinConfiguredForTests.value
        ? [
            { key: "jellyfin_url", value: "http://jellyfin:8096" },
            { key: "jellyfin_api_key", value: "", set: true },
          ]
        : [{ key: "jellyfin_url", value: "" }, { key: "jellyfin_api_key", value: "", set: false }],
    });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusResponse(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/library/verify-all") {
        return { progress: { running: false, started_at: null, processed: 0, total: 0, batch: 0, stop_reason: null }, last_summary: null };
      }
      if (url === "/api/v1/library/review") {
        return {
          series: [
            reviewSeries({ key: "film-problem", media_type: "film", title: "Entrée Film P", problem: "mismatch", media_id: 201 }),
            reviewSeries({ key: "series-problem", media_type: "series", title: "Entrée Série P", problem: "duplicate", media_id: 202 }),
            reviewSeries({ key: "film-calm", media_type: "film", title: "Entrée Film C", media_id: 301 }),
            reviewSeries({ key: "series-calm", media_type: "series", title: "Entrée Série C", media_id: 302 }),
          ],
          empty: [],
          mismatch: 0,
          duplicate_folders: 0,
          unmatched: 0,
          empty_count: 0,
        };
      }
      if (url === "/api/v1/library/regroup") {
        return emptyRegroup;
      }
      if (url === "/api/v1/library/jellyfin") {
        return emptyAlign;
      }
      if (url === "/api/v1/library/incomplete") {
        return emptyIncomplete;
      }
      if (url === "/api/v1/media") {
        return { items: [] };
      }
      throw new Error(`unexpected api ${url}`);
    });
  });

  it("sépare films et séries, onglets et en-tête disque", async () => {
    const wrapper = mount(LibraryView, {
      global: {
        components: {
          RouterLink: { template: "<a><slot /></a>" },
        },
      },
    });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain("Fichiers sur le disque uniquement");
    expect(text).toContain("Films à ranger");
    expect(text).toContain("Séries à ranger");
    expect(text).toContain("Films OK");
    expect(text).toContain("Séries OK");
    expect(text).toContain("À traiter");
    expect(text).not.toContain("Importer l'historique");
    expect(text).not.toContain("Séries sans problème");

    const problemFilms = wrapper.find('[data-library-section="problems-films"]');
    const problemSeries = wrapper.find('[data-library-section="problems-series"]');
    const contentFilms = wrapper.find('[data-library-section="content-films"]');
    const contentSeries = wrapper.find('[data-library-section="content-series"]');

    expect(problemFilms.exists()).toBe(true);
    expect(problemSeries.exists()).toBe(true);
    expect(contentFilms.exists()).toBe(true);
    expect(contentSeries.exists()).toBe(true);

    expect(problemFilms.text()).toContain("Entrée Film P");
    expect(problemSeries.text()).toContain("Entrée Série P");
    expect(contentFilms.text()).toContain("Entrée Film C");
    expect(contentSeries.text()).toContain("Entrée Série C");

    expect(text).toMatch(/à traiter/);
    expect(text).toContain("Relire les incomplets");
  });
});

describe("LibraryView — alignement Jellyfin", () => {
  beforeEach(() => {
    apiMock.mockReset();
    fetchSettingsMock.mockReset();
    resetServiceStatusForTests();
    jellyfinConfiguredForTests.value = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "jellyfin_url", value: "" },
        { key: "jellyfin_api_key", value: "", set: false },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusResponse(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/library/verify-all") {
        return { progress: { running: false, started_at: null, processed: 0, total: 0, batch: 0, stop_reason: null }, last_summary: null };
      }
      if (url === "/api/v1/library/review") {
        return {
          series: [reviewSeries({ key: "series-calm", media_type: "series", title: "Entrée Série C", media_id: 302 })],
          empty: [],
          mismatch: 0,
          duplicate_folders: 0,
          unmatched: 0,
          empty_count: 0,
        };
      }
      if (url === "/api/v1/library/regroup") {
        return emptyRegroup;
      }
      if (url === "/api/v1/library/jellyfin") {
        return emptyAlign;
      }
      if (url === "/api/v1/library/incomplete") {
        return emptyIncomplete;
      }
      if (url === "/api/v1/media") {
        return { items: [] };
      }
      throw new Error(`unexpected api ${url}`);
    });
  });

  it("masque alignement Jellyfin et le filtre associé sans configuration", async () => {
    const wrapper = mount(LibraryView, {
      global: {
        components: {
          RouterLink: { template: "<a><slot /></a>" },
        },
      },
    });
    await flushPromises();
    expect(wrapper.find('[data-library-section="align"]').exists()).toBe(false);
    expect(wrapper.text()).not.toContain("Suivi par Jellyfin");
    expect(wrapper.text()).toContain("Séries OK");
  });
});

describe("LibraryView — rétention corbeille", () => {
  beforeEach(() => {
    apiMock.mockReset();
    fetchSettingsMock.mockReset();
    resetServiceStatusForTests();
    jellyfinConfiguredForTests.value = true;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "trash_retention_days", value: 3 },
        { key: "jellyfin_url", value: "http://jellyfin:8096" },
        { key: "jellyfin_api_key", value: "", set: true },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusResponse(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/library/verify-all") {
        return { progress: { running: false, started_at: null, processed: 0, total: 0, batch: 0, stop_reason: null }, last_summary: null };
      }
      if (url === "/api/v1/library/review") {
        return {
          series: [],
          empty: [],
          mismatch: 0,
          duplicate_folders: 0,
          unmatched: 0,
          empty_count: 0,
        };
      }
      if (url === "/api/v1/library/regroup") {
        return emptyRegroup;
      }
      if (url === "/api/v1/library/jellyfin") {
        return emptyAlign;
      }
      if (url === "/api/v1/library/incomplete") {
        return { ...emptyIncomplete, files: [oneIncompleteFile] };
      }
      if (url === "/api/v1/media") {
        return { items: [] };
      }
      throw new Error(`unexpected api ${url}`);
    });
  });

  it("renvoie la conservation corbeille vers les réglages", async () => {
    const wrapper = mount(LibraryView, {
      global: {
        components: {
          RouterLink: { template: "<a><slot /></a>" },
        },
      },
    });
    await flushPromises();
    await wrapper.find('[data-library-tab="nettoyage"]').trigger("click");
    const selectAll = wrapper.findAll("button").find((b) => b.text() === "Tout sélectionner");
    await selectAll?.trigger("click");
    await wrapper.find("[data-incomplete-preview]").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Réglages");
    expect(wrapper.text()).toContain("Corbeille");
    expect(wrapper.text()).not.toContain("pendant 3 jours");
  });
});

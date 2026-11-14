import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../components/PosterCard.vue", () => ({
  default: { template: "<div><slot name='meta' /></div>" },
}));
vi.mock("../components/HomeGridSection.vue", () => ({
  default: {
    props: ["title"],
    template: "<div><h2>{{ title }}</h2><slot /></div>",
  },
}));
vi.mock("../components/HomeTechnicalFooter.vue", () => ({
  default: { template: "<div class='home-technical'>État technique</div>" },
}));
vi.mock("../components/MediaQuickView.vue", () => ({
  default: { template: "<span />" },
}));
vi.mock("../components/StatusIcon.vue", () => ({
  default: { template: "<span />" },
}));

import TmdbHomeView from "./TmdbHomeView.vue";

const TMDB_HOME = {
  cinema_films: [
    {
      tmdb_id: 1,
      media_type: "film",
      title: "Film Démo",
      vote_average: 7.1,
      followed: false,
      in_library: false,
    },
  ],
  streaming_films: [],
  physical_films: [],
  new_series: [],
  on_air_series: [],
  hive_dispos: [],
};

const DASHBOARD = {
  to_watch_groups: [
    {
      media_id: 10,
      media_title: "Œuvre Démo",
      media_type: "series",
      count: 2,
      label: "2 épisodes",
      items: [],
    },
  ],
  upcoming_week: [
    {
      media_id: 11,
      media_title: "Série Suivie",
      media_type: "series",
      season: 1,
      episode: 3,
      title: "Épisode démo",
      air_date: "2099-06-01",
      downloaded: false,
      watched: false,
    },
  ],
  recent_downloads: [
    {
      media_id: 12,
      media_title: "Arrivée Démo",
      media_type: "film",
      season: null,
      episode: null,
      title: null,
      downloaded_at: "2099-05-01T12:00:00Z",
    },
  ],
  progress: [
    {
      media_id: 13,
      media_title: "Suivi Démo",
      watched: 2,
      total: 5,
      media_type: "series",
    },
  ],
  downloads_active: [
    {
      id: 1,
      state: "downloading",
      progress: 42,
      speed: null,
      eta_seconds: null,
      error_code: null,
      error: null,
      media_title: "Téléchargement Démo",
      season: 1,
      episode: 1,
    },
  ],
  downloads_failed: [],
};

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation((path: string) => {
    if (path === "/api/v1/tmdb-trial/home") {
      return Promise.resolve(TMDB_HOME);
    }
    if (path === "/api/v1/dashboard") {
      return Promise.resolve(DASHBOARD);
    }
    return Promise.reject(new Error(`unexpected ${path}`));
  });
});

describe("TmdbHomeView", () => {
  it("affiche une rangée TMDB avec note", async () => {
    const wrapper = mount(TmdbHomeView);
    await flushPromises();
    expect(wrapper.text()).toContain("Accueil");
    expect(wrapper.text()).not.toContain("Accueil TMDB");
    expect(wrapper.text()).toContain("TMDB 7,1/10");
    expect(apiMock).toHaveBeenCalledWith("/api/v1/tmdb-trial/home");
    expect(apiMock).toHaveBeenCalledWith("/api/v1/dashboard");
  });

  it("affiche les blocs personnels au-dessus des rangées TMDB", async () => {
    const wrapper = mount(TmdbHomeView);
    await flushPromises();
    const text = wrapper.text();
    expect(text).toContain("Progression");
    expect(text).toContain("Prochains épisodes");
    expect(text).toContain("Derniers téléchargements");
    expect(text).toContain("À voir");
    expect(text).toContain("État technique");
    expect(text).toContain("Films au cinéma");
    expect(text.indexOf("Progression")).toBeLessThan(text.indexOf("Films au cinéma"));
    expect(text.indexOf("À voir")).toBeLessThan(text.indexOf("Films au cinéma"));
  });
});

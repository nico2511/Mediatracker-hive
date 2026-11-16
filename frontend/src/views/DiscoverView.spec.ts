import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../components/TmdbPosterImg.vue", () => ({
  default: { template: "<div />" },
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ back: vi.fn(), push: vi.fn() }),
}));

vi.mock("../components/MediaQuickView.vue", () => ({
  default: {
    props: {
      target: { default: null },
      follow: { type: Boolean, default: false },
      stackOnTop: { type: Boolean, default: false },
    },
    emits: ["close", "changed"],
    template: "<div v-if='target' class='quick' />",
  },
}));

import MediaQuickView from "../components/MediaQuickView.vue";
import DiscoverView from "./DiscoverView.vue";
import { DISCOVER_GESTURES_KEY } from "../discoverCache";

const filmCard = {
  tmdb_id: 10,
  media_type: "film",
  title: "Film Démo",
  overview: "Synopsis démo.",
  followed: false,
  tmdb_url: "https://www.themoviedb.org/movie/10",
};

const seriesCard = {
  tmdb_id: 20,
  media_type: "series",
  title: "Série Démo",
  overview: "Texte.",
  followed: false,
  tmdb_url: "https://www.themoviedb.org/tv/20",
};

function homePayload(film = true, series = false) {
  return {
    cinema_films: film ? [filmCard] : [],
    streaming_films: [],
    physical_films: [],
    new_series: series ? [seriesCard] : [],
    on_air_series: [],
  };
}

async function swipeCard(wrapper: ReturnType<typeof mount>, dx: number, dy: number) {
  const card = wrapper.find(".discover-card");
  await card.trigger("pointerdown", { clientX: 0, clientY: 0, button: 0, pointerId: 1 });
  await card.trigger("pointermove", { clientX: dx, clientY: dy, pointerId: 1 });
  await card.trigger("pointerup", { clientX: dx, clientY: dy, pointerId: 1 });
  await flushPromises();
}

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem(DISCOVER_GESTURES_KEY, "1");
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => {
    if (url.includes("/tmdb-trial/home")) {
      return homePayload();
    }
    if (url === "/api/v1/media") {
      return { id: 99 };
    }
    return {};
  });
});

describe("DiscoverView", () => {
  it("n’affiche l’aide qu’une seule fois", async () => {
    localStorage.removeItem(DISCOVER_GESTURES_KEY);
    const wrapper = mount(DiscoverView);
    await flushPromises();
    expect(wrapper.text()).toContain("Les gestes");
    await wrapper.find(".discover-help .discover-apply").trigger("click");
    expect(localStorage.getItem(DISCOVER_GESTURES_KEY)).toBe("1");
    wrapper.unmount();
    const again = mount(DiscoverView);
    await flushPromises();
    expect(again.text()).not.toContain("Les gestes");
  });

  it("un film suit via POST /api/v1/media", async () => {
    const wrapper = mount(DiscoverView);
    await flushPromises();
    await swipeCard(wrapper, 120, 0);
    const created = apiMock.mock.calls.find((call) => call[0] === "/api/v1/media");
    expect(created).toBeTruthy();
    const body = JSON.parse(String((created?.[1] as RequestInit).body));
    expect(body).toMatchObject({ type: "film", start_now: true, tmdb_id: 10 });
  });

  it("passe MediaQuickView au-dessus de l’écran immersif", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("/tmdb-trial/home")) {
        return homePayload(false, true);
      }
      return {};
    });
    const wrapper = mount(DiscoverView);
    await flushPromises();
    await swipeCard(wrapper, 120, 0);
    expect(wrapper.find(".quick").exists()).toBe(true);
    expect(wrapper.findComponent(MediaQuickView).props("stackOnTop")).toBe(true);
  });

  it("affiche synopsis et lien TMDB dans la fiche", async () => {
    const wrapper = mount(DiscoverView);
    await flushPromises();
    await swipeCard(wrapper, 0, -120);
    expect(wrapper.text()).toContain("Synopsis démo.");
    const link = wrapper.find("a.discover-tmdb");
    expect(link.attributes("href")).toBe("https://www.themoviedb.org/movie/10");
    expect(link.attributes("target")).toBe("_blank");
  });

  it("referme la petite fiche (bouton, tap extérieur, glissement bas)", async () => {
    const wrapper = mount(DiscoverView);
    await flushPromises();
    await swipeCard(wrapper, 0, -120);
    expect(wrapper.find(".discover-sheet").exists()).toBe(true);
    await wrapper.find(".discover-sheet-close").trigger("click");
    expect(wrapper.find(".discover-sheet").exists()).toBe(false);

    await swipeCard(wrapper, 0, -120);
    await wrapper.find(".discover-sheet").trigger("click");
    expect(wrapper.find(".discover-sheet").exists()).toBe(false);

    await swipeCard(wrapper, 0, -120);
    await wrapper.find(".discover-sheet").trigger("pointerdown", { clientY: 10, button: 0 });
    await wrapper.find(".discover-sheet").trigger("pointerup", { clientY: 100, button: 0 });
    expect(wrapper.find(".discover-sheet").exists()).toBe(false);
  });

  it("affiche l’erreur de suivi au-dessus de la carte", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("/tmdb-trial/home")) {
        return homePayload();
      }
      if (url === "/api/v1/media") {
        return Promise.reject(new Error("fail"));
      }
      return {};
    });
    const wrapper = mount(DiscoverView);
    await flushPromises();
    await swipeCard(wrapper, 120, 0);
    const error = wrapper.find(".discover-error");
    expect(error.exists()).toBe(true);
    expect(error.text()).toContain("Suivi impossible.");
    expect(wrapper.find(".discover-card").exists()).toBe(true);
  });

  it("évite le double POST film", async () => {
    let posts = 0;
    apiMock.mockImplementation((url: string) => {
      if (url.includes("/tmdb-trial/home")) {
        return Promise.resolve(homePayload());
      }
      if (url === "/api/v1/media") {
        posts += 1;
        return new Promise((resolve) => {
          setTimeout(() => resolve({ id: posts }), 40);
        });
      }
      return Promise.resolve({});
    });
    const wrapper = mount(DiscoverView);
    await flushPromises();
    const card = wrapper.find(".discover-card");
    await card.trigger("pointerdown", { clientX: 0, clientY: 0, button: 0, pointerId: 1 });
    await card.trigger("pointermove", { clientX: 120, clientY: 0, pointerId: 1 });
    await card.trigger("pointerup", { clientX: 120, clientY: 0, pointerId: 1 });
    await card.trigger("pointerdown", { clientX: 0, clientY: 0, button: 0, pointerId: 2 });
    await card.trigger("pointermove", { clientX: 120, clientY: 0, pointerId: 2 });
    await card.trigger("pointerup", { clientX: 120, clientY: 0, pointerId: 2 });
    await flushPromises();
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(posts).toBe(1);
  });

  it("Plus tard remet la carte en fin de pile sans localStorage", async () => {
    const extra = { ...filmCard, tmdb_id: 11, title: "Film B" };
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("/tmdb-trial/home")) {
        return {
          ...homePayload(),
          streaming_films: [extra],
        };
      }
      return {};
    });
    const wrapper = mount(DiscoverView);
    await flushPromises();
    expect(wrapper.text()).toContain("Film Démo");
    await swipeCard(wrapper, 0, 120);
    expect(wrapper.text()).toContain("Film B");
    const passRaw = localStorage.getItem("mt.discover.pass.v1") || "[]";
    const pass = JSON.parse(passRaw) as { key: string }[];
    expect(pass.some((row) => row.key === "film:10")).toBe(false);
  });

  it("garde les cartes Plus tard après réapplication des filtres", async () => {
    const extra = { ...filmCard, tmdb_id: 11, title: "Film B" };
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("/tmdb-trial/home")) {
        return {
          ...homePayload(),
          streaming_films: [extra],
        };
      }
      return {};
    });
    const wrapper = mount(DiscoverView);
    await flushPromises();
    await swipeCard(wrapper, 0, 120);
    expect(wrapper.text()).toContain("Film B");
    await wrapper.find(".discover-icon[aria-label='Filtres']").trigger("click");
    await wrapper.find(".discover-apply").trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Film B");
    await swipeCard(wrapper, 0, 120);
    expect(wrapper.text()).toContain("Film Démo");
  });
});
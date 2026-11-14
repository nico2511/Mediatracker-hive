import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, userFacingApiError } from "../../api";

const apiMock = vi.hoisted(() => vi.fn());
const routeRef = vi.hoisted(() => ({ params: { workKey: "wk-film" } }));
const routerBack = vi.hoisted(() => vi.fn());

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    api: (...args: unknown[]) => apiMock(...args),
  };
});

vi.mock("vue-router", () => ({
  useRoute: () => routeRef,
  useRouter: () => ({ back: routerBack }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import HiveWorkView from "./HiveWorkView.vue";

const FILM_WORK = {
  work_key: "wk-film",
  title: "Œuvre fictive A",
  type: "film",
  year: 2099,
  poster_url: null,
  overview: "Résumé court.",
  disponible: true,
  in_library: false,
  card_state: "available",
  state_label: "Disponible",
  state_label_short: "Dispo.",
  progress_pct: null,
  error_line: null,
  followed: false,
  shared_by_me: false,
  on_disk: false,
  tone: "ok",
  season: null,
  episode: null,
  media_id: 12,
  media_item_id: 34,
  episode_count: null,
  episodes: null,
  versions: [
    {
      version_key: "HD+VFF",
      disponible: true,
      source_count: 2,
      quality: "HD",
      language: "VFF",
      recommended: true,
      size_bytes: 1_000_000,
      ruche_count: 1,
    },
    {
      version_key: "4K+VOSTFR",
      disponible: true,
      source_count: 1,
      quality: "4K",
      language: "VOSTFR",
      recommended: false,
    },
  ],
};

const SERIES_WORK = {
  ...FILM_WORK,
  work_key: "wk-series",
  type: "series",
  title: "Série fictive",
  card_state: "waiting",
  state_label: "En attente",
  tone: "warn",
  episodes: [
    {
      ...FILM_WORK,
      work_key: "wk-ep1",
      season: 1,
      episode: 1,
      card_state: "available",
      state_label: "Disponible",
      tone: "ok",
    },
  ],
  episode_count: 1,
};

beforeEach(() => {
  apiMock.mockReset();
  routeRef.params = { workKey: "wk-film" };
});

describe("HiveWorkView catalogue request errors", () => {
  it("affiche le détail français renvoyé par l’API (422)", () => {
    const detail = "Cette œuvre est déjà dans votre bibliothèque.";
    const err = new ApiError(422, detail);
    expect(userFacingApiError(err, "Transfert indisponible.")).toBe(detail);
  });
});

describe("HiveWorkView fiche", () => {
  it("affiche le bandeau fiche-hero et l’état catalogue", async () => {
    apiMock.mockResolvedValue(FILM_WORK);
    const w = mount(HiveWorkView);
    await flushPromises();
    expect(w.find("[data-testid=hive-work-hero]").exists()).toBe(true);
    expect(w.text()).toContain("Disponible");
    expect(w.get("[data-testid=hive-work-actions-menu]").exists()).toBe(true);
    expect(w.get("[data-testid=hive-work-version-request]").text()).toContain("HD");
    expect(w.get("[data-testid=hive-work-version-request]").text()).toContain("VFF");
    expect(w.text()).toContain("Conseillée");
  });

  it("liste les épisodes pour une fiche série agrégée", async () => {
    routeRef.params = { workKey: "wk-series" };
    apiMock.mockResolvedValue(SERIES_WORK);
    const w = mount(HiveWorkView);
    await flushPromises();
    expect(w.text()).toContain("Épisodes catalogue");
    expect(w.text()).toContain("S1 · E01");
    expect(w.find("[data-testid=hive-work-request]").exists()).toBe(false);
  });
});

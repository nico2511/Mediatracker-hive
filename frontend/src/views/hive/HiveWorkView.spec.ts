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
      profile_compatible: true,
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
      profile_compatible: false,
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
    const w = mountWork();
    await flushPromises();
    expect(w.find("[data-testid=hive-work-hero]").exists()).toBe(true);
    expect(w.text()).toContain("Disponible");
    expect(w.get("[data-testid=hive-work-actions-menu]").exists()).toBe(true);
    expect(w.get('[data-testid="hive-version-download-HD+VFF"]').exists()).toBe(true);
    expect(w.text()).toContain("Conseillée");
  });

  const mountWork = () =>
    mount(HiveWorkView, {
      global: { stubs: { Teleport: true, RouterLink: true }, attachTo: document.body },
    });

  it("requestFilmAuto n’envoie pas version_key", async () => {
    apiMock.mockResolvedValue({ ...FILM_WORK, versions: [] });
    const w = mountWork();
    await flushPromises();
    apiMock.mockClear();
    await w.get("[data-testid=hive-work-actions-menu] button").trigger("click");
    await w.get("[data-testid=hive-work-request]").trigger("click");
    await flushPromises();
    const post = apiMock.mock.calls.find(
      (args) => typeof args[0] === "string" && String(args[0]).includes("/request"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(String((post?.[1] as { body?: string })?.body))).not.toHaveProperty("version_key");
  });

  it("affiche la confirmation héros pour une version hors profil sélectionnée", async () => {
    apiMock.mockResolvedValue({
      ...FILM_WORK,
      versions: [
        {
          version_key: "4K+VOSTFR",
          disponible: true,
          source_count: 1,
          profile_compatible: false,
        },
      ],
    });
    const w = mountWork();
    await flushPromises();
    await w.get("[data-testid=hive-work-actions-menu] button").trigger("click");
    await w.get("[data-testid=hive-work-request]").trigger("click");
    await flushPromises();
    expect(w.get("[data-testid=hive-hero-version-confirm]").text()).toContain("Télécharger quand même");
    expect(w.get("[data-testid=hive-hero-cancel-confirm]").exists()).toBe(true);
  });

  it("liste les épisodes pour une fiche série agrégée", async () => {
    routeRef.params = { workKey: "wk-series" };
    apiMock.mockResolvedValue(SERIES_WORK);
    const w = mountWork();
    await flushPromises();
    expect(w.text()).toContain("Épisodes catalogue");
    expect(w.text()).toContain("S1 · E01");
    expect(w.find("[data-testid=hive-work-request]").exists()).toBe(false);
  });

  it("confirme hors profil une seule fois (version) avant requête épisode", async () => {
    routeRef.params = { workKey: "wk-series" };
    apiMock.mockResolvedValue({
      ...SERIES_WORK,
      versions: [
        {
          version_key: "HD+VFF",
          disponible: true,
          source_count: 1,
          profile_compatible: true,
          recommended: true,
        },
        {
          version_key: "4K+VOSTFR",
          disponible: true,
          source_count: 1,
          profile_compatible: false,
        },
      ],
    });
    const w = mountWork();
    await flushPromises();
    const list = w.get("[data-testid=hive-catalogue-version-list]");
    const offBtn = list
      .findAll("button")
      .find((b) => b.text().trim() === "Télécharger" && !b.classes().includes("primary"));
    expect(offBtn).toBeTruthy();
    await offBtn!.trigger("click");
    await list.get("[data-testid=hive-version-confirm-btn]").trigger("click");
    await flushPromises();
    const epBtn = w.get("[data-testid=hive-episode-request]");
    await epBtn.trigger("click");
    await flushPromises();
    const post = apiMock.mock.calls.find(
      (args) => typeof args[0] === "string" && String(args[0]).includes("/request"),
    );
    expect(post).toBeTruthy();
    expect(epBtn.text()).toBe("Télécharger");
  });

  it("demande confirmation épisode si la version par défaut est hors profil", async () => {
    routeRef.params = { workKey: "wk-series" };
    apiMock.mockResolvedValue({
      ...SERIES_WORK,
      versions: [
        {
          version_key: "4K+VOSTFR",
          disponible: true,
          source_count: 1,
          profile_compatible: false,
        },
      ],
    });
    const w = mountWork();
    await flushPromises();
    const epBtn = w.get("[data-testid=hive-episode-request]");
    expect(epBtn.text()).toBe("Télécharger");
    await epBtn.trigger("click");
    await flushPromises();
    expect(epBtn.text()).toBe("Confirmer");
    expect(w.get("[data-testid=hive-episode-version-confirm]").text()).toContain("Télécharger quand même");
    apiMock.mockClear();
    await w.get("[data-testid=hive-episode-cancel-confirm]").trigger("click");
    await flushPromises();
    expect(
      apiMock.mock.calls.some(
        (args) => typeof args[0] === "string" && String(args[0]).includes("/request"),
      ),
    ).toBe(false);
    expect(epBtn.text()).toBe("Télécharger");
    await epBtn.trigger("click");
    await flushPromises();
    await epBtn.trigger("click");
    await flushPromises();
    const post = apiMock.mock.calls.find(
      (args) => typeof args[0] === "string" && String(args[0]).includes("/request"),
    );
    expect(post).toBeTruthy();
    expect(JSON.parse(String((post?.[1] as { body?: string })?.body)).version_key).toBe("4K+VOSTFR");
  });
});

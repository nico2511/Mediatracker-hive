import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const fetchAllMediaMock = vi.hoisted(() => vi.fn());
const saveSnapshot = vi.hoisted(() => vi.fn());
const snapshotRows = vi.hoisted(() => [] as { id: number; type: string; title: string; year: number; poster_url: null; on_hold: boolean; followed: boolean; poll: { allowed: boolean; reason: string } }[]);

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../pwa/connectivity", () => ({
  useConnectivity: () => ({
    offlineShell: ref(false),
    serverActionsDisabled: ref(false),
  }),
}));

vi.mock("../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: ref(false) }),
}));

const routeQuery = vi.hoisted(() => ({ value: {} as Record<string, string> }));
const routerReplace = vi.hoisted(() => vi.fn());

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>" },
  useRoute: () => ({ query: routeQuery.value }),
  useRouter: () => ({ replace: routerReplace }),
}));

vi.mock("../pwa/offlineSnapshot", () => ({
  loadMediaListSnapshot: () =>
  snapshotRows.length
    ? { saved_at: "2099-06-01T12:00:00Z", body: { items: snapshotRows } }
    : null,
  saveMediaListSnapshot: saveSnapshot,
}));

const defaultMediaRows = [
  {
    id: 1,
    type: "film",
    title: "Fiche A",
    year: 2090,
    poster_url: null,
    on_hold: false,
    followed: true,
    poll: { allowed: false, reason: "" },
  },
  {
    id: 2,
    type: "series",
    title: "Fiche B",
    year: 2091,
    poster_url: null,
    on_hold: false,
    followed: false,
    poll: { allowed: false, reason: "" },
  },
] as const;

vi.mock("../media/fetchAllMedia", () => ({
  fetchAllMedia: (...args: unknown[]) => fetchAllMediaMock(...args),
}));

vi.mock("../clientConstants", () => ({
  hiveMediaSummaryLimit: () => 24,
}));

vi.mock("../media/hiveMediaSummaries", () => ({
  fetchHiveMediaSummaries: vi.fn(),
  releaseLoadingThenRefreshHive: async (setLoading: (v: boolean) => void) => {
    setLoading(false);
  },
}));

vi.mock("../components/PosterCard.vue", () => ({
  default: {
    props: ["title", "ficheTo", "hideMoreButton"],
    emits: ["open"],
    template: `<button type="button" class="poster-stub" @click="$emit('open')">{{ title }}</button>`,
  },
}));

vi.mock("../components/MediaQuickView.vue", () => ({
  default: { template: "<div />" },
}));

vi.mock("../components/StatusIcon.vue", () => ({
  default: { template: "<span />" },
}));

import DeleteFicheDialog from "../components/DeleteFicheDialog.vue";
import MediaListView from "./MediaListView.vue";

beforeEach(() => {
  apiMock.mockReset();
  saveSnapshot.mockReset();
  snapshotRows.length = 0;
  routeQuery.value = {};
  routerReplace.mockReset();
  fetchAllMediaMock.mockImplementation(
    async (_api: unknown, _abort: () => boolean, onPartial: (rows: unknown[]) => void) => {
      const rows = [...defaultMediaRows];
      onPartial(rows);
      return rows;
    },
  );
});

describe("MediaListView filtres", () => {
  it("affiche Lien trouvé et pas Suivi par Jellyfin", async () => {
    const wrapper = mount(MediaListView);
    await flushPromises();
    expect(wrapper.text()).toContain("Lien trouvé");
    expect(wrapper.text()).not.toContain("Suivi par Jellyfin");
  });

  it("affiche Ne plus suivre quand Origine vaut Identifiés bibliothèque", async () => {
    const wrapper = mount(MediaListView);
    await flushPromises();
    expect(wrapper.text()).not.toContain("Ne plus suivre ces fiches");
    const originSelects = wrapper.findAll("select").filter((row) => row.html().includes("Identifiés bibliothèque"));
    expect(originSelects.length).toBeGreaterThan(0);
    await originSelects[0].setValue("identification");
    await flushPromises();
    expect(wrapper.text()).toContain("Ne plus suivre ces fiches");
  });

  it("translate etat=identified vers origine identification", async () => {
    routeQuery.value = { etat: "identified" };
    mount(MediaListView);
    await flushPromises();
    expect(routerReplace).toHaveBeenCalledWith(
      expect.objectContaining({
        query: expect.objectContaining({ origine: "identification" }),
      }),
    );
    const etatArg = routerReplace.mock.calls[0]?.[0] as { query?: Record<string, string> };
    expect(etatArg.query?.etat).toBeUndefined();
  });

  it("place Sélectionner hors de la rangée des filtres d'état", async () => {
    const wrapper = mount(MediaListView);
    await flushPromises();
    const row = wrapper.find(".media-library-state-filters");
    const selectRow = wrapper.find(".media-library-select-action");
    expect(row.exists()).toBe(true);
    expect(selectRow.exists()).toBe(true);
    expect(row.find(".media-library-select-action").exists()).toBe(true);
    const filterButtons = row.find(".media-library-filters").findAll("button");
    expect(filterButtons.some((b) => b.text() === "Sélectionner")).toBe(false);
    expect(selectRow.find("button").text()).toBe("Sélectionner");
  });
});

describe("MediaListView chargement", () => {
  it("affiche le snapshot avant la fin du chargement réseau", async () => {
    snapshotRows.push(
      {
        id: 9,
        type: "film",
        title: "Snapshot",
        year: 2088,
        poster_url: null,
        on_hold: false,
        followed: true,
        poll: { allowed: false, reason: "" },
      },
    );
    fetchAllMediaMock.mockImplementation(
      async (_api: unknown, _abort: () => boolean, onPartial: (rows: unknown[]) => void) => {
        await new Promise((resolve) => setTimeout(resolve, 80));
        const rows = [...defaultMediaRows];
        onPartial(rows);
        return rows;
      },
    );
    const wrapper = mount(MediaListView);
    await flushPromises();
    expect(wrapper.text()).toContain("Snapshot");
    expect(wrapper.text()).toContain("Chargement");
    await new Promise((resolve) => setTimeout(resolve, 100));
    await flushPromises();
    expect(wrapper.text()).toContain("Fiche A");
  });

  it("n'affiche pas « Aucun suivi » quand le chargement échoue avec des données", async () => {
    snapshotRows.push(
      {
        id: 3,
        type: "series",
        title: "Reste affiché",
        year: 2092,
        poster_url: null,
        on_hold: false,
        followed: true,
        poll: { allowed: false, reason: "" },
      },
    );
    fetchAllMediaMock.mockRejectedValue(new Error("network down"));
    const wrapper = mount(MediaListView);
    await flushPromises();
    expect(wrapper.text()).toContain("Reste affiché");
    expect(wrapper.text()).not.toContain("Aucun suivi");
    expect(wrapper.text()).toContain("Mise à jour impossible");
  });
});

describe("MediaListView bulk selection", () => {
  it("active la sélection, sélectionne et appelle la suppression en masse", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/media/bulk") {
        return { deleted: [1] };
      }
      return {};
    });
    const wrapper = mount(MediaListView);
    await flushPromises();

    const selectBtn = wrapper.findAll("button").find((b) => b.text() === "Sélectionner");
    expect(selectBtn).toBeTruthy();
    await selectBtn!.trigger("click");

    expect(wrapper.text()).toContain("0 sélectionnée");
    const topBar = wrapper.find(".media-library-bulk-bar--top");
    await topBar.findAll("button").find((b) => b.text() === "Tout sélectionner")!.trigger("click");
    expect(wrapper.text()).toContain("2 sélectionnées");

    await topBar.findAll("button").find((b) => b.text() === "Supprimer les fiches")!.trigger("click");
    const dialog = wrapper.findComponent(DeleteFicheDialog);
    expect(dialog.props("open")).toBe(true);
    expect(dialog.props("count")).toBe(2);

    await dialog.find("form.dialog").trigger("submit.prevent");
    await flushPromises();

    expect(apiMock).toHaveBeenCalledWith(
      "/api/v1/media/bulk",
      expect.objectContaining({
        method: "DELETE",
        body: JSON.stringify({ ids: [1, 2], trash: false }),
      }),
    );
    expect(wrapper.text()).toContain("1 fiche supprimée, 1 en échec.");
    expect(wrapper.findAll("button").some((b) => b.text() === "Sélectionner")).toBe(true);
  });

  it("quitte le mode sélection avec Échap", async () => {
    const wrapper = mount(MediaListView);
    await flushPromises();
    await wrapper.findAll("button").find((b) => b.text() === "Sélectionner")!.trigger("click");
    expect(wrapper.find(".media-library-bulk-bar--top").exists()).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(wrapper.find(".media-library-bulk-bar--top").exists()).toBe(false);
  });
});

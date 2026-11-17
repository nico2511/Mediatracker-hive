import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

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

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>" },
}));

vi.mock("../pwa/offlineSnapshot", () => ({
  loadMediaListSnapshot: () => null,
  saveMediaListSnapshot: vi.fn(),
}));

vi.mock("../media/fetchAllMedia", () => ({
  fetchAllMedia: async (_api: unknown, _abort: () => boolean, onPartial: (rows: unknown[]) => void) => {
    const rows = [
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
    ];
    onPartial(rows);
    return rows;
  },
}));

vi.mock("../media/hiveMediaSummaries", () => ({
  fetchHiveMediaSummaries: vi.fn(),
  HIVE_MEDIA_SUMMARY_LIMIT: 24,
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

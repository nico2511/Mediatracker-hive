import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fetchAllMediaMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: vi.fn(),
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
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}));

vi.mock("../pwa/offlineSnapshot", () => ({
  loadMediaListSnapshot: () => null,
  saveMediaListSnapshot: vi.fn(),
}));

vi.mock("../media/fetchAllMedia", () => ({
  fetchAllMedia: (...args: unknown[]) => fetchAllMediaMock(...args),
}));

vi.mock("../clientConstants", () => ({ hiveMediaSummaryLimit: () => 24 }));
vi.mock("../media/hiveMediaSummaries", () => ({
  fetchHiveMediaSummaries: vi.fn(),
  releaseLoadingThenRefreshHive: async (setLoading: (v: boolean) => void) => {
    setLoading(false);
  },
}));

vi.mock("../components/PosterCard.vue", () => ({
  default: {
    template: `<div class="poster-stub"><slot name="meta" /></div>`,
  },
}));

vi.mock("../components/MediaQuickView.vue", () => ({ default: { template: "<div />" } }));
vi.mock("../components/StatusIcon.vue", () => ({ default: { template: "<span />" } }));
vi.mock("../components/DeleteFicheDialog.vue", () => ({ default: { template: "<div />" } }));
vi.mock("../components/HiveBulkFicheShareDialog.vue", () => ({ default: { template: "<div />" } }));
vi.mock("../components/MediaLibraryBulkSelectionBar.vue", () => ({ default: { template: "<div />" } }));
vi.mock("../components/FilterHelpHint.vue", () => ({ default: { template: "<span />" } }));

import MediaListView from "./MediaListView.vue";

describe("MediaListView note TMDB", () => {
  beforeEach(() => {
    fetchAllMediaMock.mockImplementation(async (_api, _abort, onPartial: (rows: unknown[]) => void) => {
      const rows = [
        {
          id: 1,
          type: "film",
          title: "Fiche exemple A",
          year: 2090,
          poster_url: null,
          on_hold: false,
          followed: true,
          poll: { allowed: false, reason: "" },
          tmdb_vote_average: 7.4,
        },
      ];
      onPartial(rows);
      return rows;
    });
  });

  it("affiche la note sous l’année", async () => {
    const wrapper = mount(MediaListView);
    await flushPromises();
    expect(wrapper.find(".tmdb-rating-card-line").text()).toContain("7,4");
    expect(wrapper.find(".visually-hidden").text()).toBe("Note TMDB 7,4 sur 10");
  });
});
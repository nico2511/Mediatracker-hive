import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const routerPush = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: { value: true } }),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock("../../components/PosterCard.vue", () => ({
  default: {
    name: "PosterCard",
    template: `<div class="poster-card-stub"><slot name="actions" /></div>`,
  },
}));

vi.mock("../../components/HiveContextMenuItems.vue", () => ({
  default: { template: "<span />" },
}));

vi.mock("../../components/StatusIcon.vue", () => ({
  default: { template: "<span />" },
}));

vi.mock("../../components/hive/HiveCatalogueVersionList.vue", () => ({
  default: {
    props: ["workKey", "versions"],
    emits: ["requested"],
    template: `<div data-testid="hive-card-version-list" />`,
  },
}));

import HiveCatalogueView from "./HiveCatalogueView.vue";

const SERIES_AGG = {
  work_key: "wk-series",
  type: "series",
  title: "Série fictive",
  year: 2099,
  poster_url: null,
  disponible: true,
  in_library: false,
  season: null,
  episode: null,
  media_id: null,
  media_item_id: null,
  versions: [{ version_key: "HD+VFF", disponible: true, source_count: 1 }],
  episode_count: 3,
  card_state: "available",
  state_label: "Disponible",
  state_label_short: "Dispo.",
  progress_pct: null,
  error_line: null,
  followed: false,
  shared_by_me: false,
  on_disk: false,
  tone: "ok",
};

const FILM_ROW = {
  ...SERIES_AGG,
  work_key: "wk-film",
  type: "film",
  title: "Film fictif",
  episode_count: null,
  versions: [
    { version_key: "HD+VFF", disponible: true, source_count: 1, profile_compatible: true, recommended: true },
    { version_key: "4K+VO", disponible: true, source_count: 1, profile_compatible: false, recommended: false },
  ],
};

beforeEach(() => {
  apiMock.mockReset();
  routerPush.mockReset();
  apiMock.mockResolvedValue({ items: [SERIES_AGG, FILM_ROW], refreshed_at: null });
});

describe("HiveCatalogueView — carte série agrégée", () => {
  it("propose Ouvrir la fiche au lieu de Télécharger", async () => {
    const wrapper = mount(HiveCatalogueView);
    await flushPromises();

    const seriesRoot = wrapper.findAll(".poster-card-stub")[0];
    expect(seriesRoot.find('[data-testid="hive-card-open-fiche"]').exists()).toBe(true);
    expect(seriesRoot.find('[data-testid="hive-card-request"]').exists()).toBe(false);

    await seriesRoot.find('[data-testid="hive-card-open-fiche"]').trigger("click");
    expect(routerPush).toHaveBeenCalledWith("/hive/catalogue/wk-series");
    expect(apiMock).not.toHaveBeenCalledWith(
      expect.stringContaining("/request"),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("affiche une liste de versions sur un film disponible", async () => {
    const wrapper = mount(HiveCatalogueView);
    await flushPromises();

    const filmRoot = wrapper.findAll(".poster-card-stub")[1];
    expect(filmRoot.find('[data-testid="hive-card-version-list"]').exists()).toBe(true);
    expect(filmRoot.find('[data-testid="hive-card-open-fiche"]').exists()).toBe(false);
  });
});

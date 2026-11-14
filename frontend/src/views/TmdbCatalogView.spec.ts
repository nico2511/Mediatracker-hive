import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../components/PosterCard.vue", () => ({
  default: { template: "<div />" },
}));
vi.mock("../components/MediaQuickView.vue", () => ({
  default: { template: "<span />" },
}));
vi.mock("../components/StatusIcon.vue", () => ({
  default: { template: "<span />" },
}));

import TmdbCatalogView from "./TmdbCatalogView.vue";

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({
    items: [],
    total: 0,
    page: 1,
    genres: [],
    years: [],
    providers: [{ id: 8, name: "Plateforme Démo" }],
  });
});

describe("TmdbCatalogView", () => {
  it("charge la page films TMDB avec filtre release_kind", async () => {
    const wrapper = mount(TmdbCatalogView, { props: { mediaType: "film" } });
    await flushPromises();
    expect(wrapper.text()).toContain("Derniers films");
    expect(wrapper.text()).not.toContain("Derniers films TMDB");
    const url = String(apiMock.mock.calls[0]?.[0] ?? "");
    expect(url).toContain("/api/v1/tmdb-trial/browse");
    expect(url).toContain("release_kind=streaming");
    expect(url).toContain("sort=recent");
  });
});

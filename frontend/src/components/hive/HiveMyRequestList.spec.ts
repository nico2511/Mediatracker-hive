import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  api: vi.fn(),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>" },
}));

import HiveMyRequestList from "./HiveMyRequestList.vue";
import type { HiveMyRequestItem } from "../../hive/hiveRequests";

const seriesHead: HiveMyRequestItem = {
  media_id: 10,
  media_item_id: 101,
  work_key: "wk-a",
  title: "Série Exemple A",
  type: "series",
  season: 1,
  episode: 1,
  status: "pending",
  hive_request_available: false,
  poster_url: null,
  requested_at: "2026-01-02T12:00:00+00:00",
};

const seriesSecond: HiveMyRequestItem = {
  ...seriesHead,
  media_item_id: 102,
  season: 1,
  episode: 3,
  status: "transfer",
  requested_at: "2026-01-03T12:00:00+00:00",
};

const filmRow: HiveMyRequestItem = {
  media_id: 20,
  media_item_id: 201,
  work_key: "wk-b",
  title: "Film Exemple B",
  type: "film",
  season: null,
  episode: null,
  status: "pending",
  hive_request_available: false,
  poster_url: null,
  requested_at: "2026-01-01T10:00:00+00:00",
};

describe("HiveMyRequestList", () => {
  it("regroupe une série en carte dépliable avec résumé agrégé", async () => {
    const w = mount(HiveMyRequestList, {
      props: {
        items: [seriesHead, seriesSecond],
        downloads: [
          {
            media_item_id: 102,
            progress: 34,
            done_bytes: 1_200_000_000,
            size: 3_400_000_000,
            state: "downloading",
          },
        ],
      },
    });
    await flushPromises();

    const group = w.get('[data-testid="hive-mine-group-10"]');
    expect(group.text()).toContain("Série Exemple A");
    expect(group.text()).toContain("2 épisodes demandés");
    expect(group.text()).toContain("1 en attente");
    expect(group.text()).toContain("1 en transfert");
    expect(w.get('[data-testid="hive-mine-episode-102"]').text()).toContain("S1 · E03");
  });

  it("affiche un film sans chevron de série", async () => {
    const w = mount(HiveMyRequestList, {
      props: { items: [filmRow], downloads: [] },
    });
    await flushPromises();
    expect(w.get('[data-testid="hive-mine-film-20"]').text()).toContain("Film Exemple B");
    expect(w.find('[data-testid^="hive-mine-group-toggle"]').exists()).toBe(false);
  });
});

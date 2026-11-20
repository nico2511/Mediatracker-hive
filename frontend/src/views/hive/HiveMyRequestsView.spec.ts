import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>" },
}));

import HiveMyRequestsView from "./HiveMyRequestsView.vue";

beforeEach(() => {
  apiMock.mockReset();
});

describe("HiveMyRequestsView", () => {
  it("liste une demande annoncée et une invite prompt", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/hive/requests/mine") {
        return {
          items: [
            {
              media_id: 11,
              media_item_id: 21,
              work_key: "w1",
              title: "Film Exemple G",
              type: "film",
              season: null,
              episode: null,
              status: "pending",
              hive_request_available: false,
            },
            {
              media_id: 12,
              media_item_id: 22,
              work_key: "w2",
              title: "Série Exemple H",
              type: "series",
              season: 1,
              episode: 4,
              status: "prompt",
              hive_request_available: true,
            },
          ],
        };
      }
      if (url === "/api/v1/downloads") {
        return { items: [] };
      }
      throw new Error(url);
    });

    const w = mount(HiveMyRequestsView);
    await flushPromises();

    expect(w.get("[data-testid=hive-mine-list]").text()).toContain("Film Exemple G");
    expect(w.get("[data-testid=hive-mine-announce]").exists()).toBe(true);
    expect(w.get("[data-testid=hive-mine-retract]").exists()).toBe(true);
  });

  it("affiche l'état vide", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/hive/requests/mine") {
        return { items: [] };
      }
      if (url === "/api/v1/downloads") {
        return { items: [] };
      }
      throw new Error(url);
    });

    const w = mount(HiveMyRequestsView);
    await flushPromises();
    expect(w.get("[data-testid=hive-mine-empty]").text()).toContain("Aucun suivi en attente");
  });
});
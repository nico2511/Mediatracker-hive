import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const patchMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("../../settings", () => ({
  patchSettings: (...args: unknown[]) => patchMock(...args),
}));

import HiveReceivedRequestsView from "./HiveReceivedRequestsView.vue";

beforeEach(() => {
  apiMock.mockReset();
  patchMock.mockReset();
});

describe("HiveReceivedRequestsView", () => {
  it("liste une demande anonyme avec Accepter / Refuser", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/settings") {
        return { settings: [{ key: "hive_auto_accept_requests", value: false }] };
      }
      if (url === "/api/v1/hive/requests/received") {
        return {
          items: [
            {
              request_id: "req-demo-01",
              work_key: "wk1",
              title: "Les Abeilles du Nord",
              type: "film",
              season: null,
              episode: null,
              already_shared: false,
              status: "pending",
            },
          ],
        };
      }
      throw new Error(url);
    });

    const w = mount(HiveReceivedRequestsView);
    await flushPromises();

    expect(w.get("[data-testid=hive-received-list]").text()).toContain("Les Abeilles du Nord");
    expect(w.get("[data-testid=hive-received-list]").text()).toContain("Demande anonyme");
    expect(w.get("[data-testid=hive-accept-request]").exists()).toBe(true);
    expect(w.get("[data-testid=hive-refuse-request]").exists()).toBe(true);
    expect(w.text()).not.toMatch(/instance/i);
  });
});
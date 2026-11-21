import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

import HiveReceivedRequestList from "./HiveReceivedRequestList.vue";
import { hiveShellContextKey } from "../../hive/hiveShellContext";

const sampleRow = {
  request_id: "req-1",
  work_key: "wk1",
  title: "Titre démo",
  type: "film" as const,
  season: null,
  episode: null,
  already_shared: false,
  status: "pending" as const,
};

describe("HiveReceivedRequestList", () => {
  it("notifie le shell après refus", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/hive/requests/received/refuse") {
        return undefined;
      }
      throw new Error(url);
    });
    const refreshTabBadges = vi.fn();
    const reload = vi.fn(async () => []);

    const w = mount(HiveReceivedRequestList, {
      props: { rows: [sampleRow], reload },
      global: {
        provide: {
          [hiveShellContextKey as symbol]: { refreshTabBadges },
        },
      },
    });
    await w.get("[data-testid=hive-refuse-request]").trigger("click");
    await flushPromises();

    expect(reload).toHaveBeenCalled();
    expect(refreshTabBadges).toHaveBeenCalled();
  });
});
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>" },
  RouterView: { template: "<div data-testid='router-view' />" },
  useRoute: () => ({ path: "/hive/demandes" }),
}));

import HiveShell from "./HiveShell.vue";

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/hive/requests/mine") {
      return { items: [{ media_item_id: 1 }, { media_item_id: 2 }] };
    }
    if (url === "/api/v1/hive/requests/received") {
      return { items: [{ request_id: "abc" }] };
    }
    if (url === "/api/v1/hive/requests/wanted") {
      return { items: [{ work_key: "wk1" }, { work_key: "wk2" }] };
    }
    throw new Error(url);
  });
});

describe("HiveShell", () => {
  it("affiche les onglets Hive dont Recherché", async () => {
    const w = mount(HiveShell);
    await flushPromises();
    expect(w.get("[data-testid=hive-tab-catalogue]").text()).toContain("Catalogue");
    expect(w.get("[data-testid=hive-tab-demandes]").text()).toContain("Mes demandes");
    expect(w.get("[data-testid=hive-tab-demandes]").text()).toContain("2");
    expect(w.get("[data-testid=hive-tab-recues]").text()).toContain("Reçues");
    expect(w.get("[data-testid=hive-tab-recues]").text()).toContain("1");
    expect(w.get("[data-testid=hive-tab-recherche]").text()).toContain("Recherché");
    expect(w.get("[data-testid=hive-tab-recherche]").text()).toContain("2");
  });
});
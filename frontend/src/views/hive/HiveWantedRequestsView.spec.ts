import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

import HiveWantedRequestsView from "./HiveWantedRequestsView.vue";

beforeEach(() => {
  apiMock.mockReset();
});

describe("HiveWantedRequestsView", () => {
  it("affiche le texte d'aide et la liste", async () => {
    apiMock.mockResolvedValue({
      items: [
        {
          work_key: "wk1",
          title: "Le Miel d'Hiver",
          year: 2024,
          type: "film",
          season: null,
          episode: null,
          requester_count: 3,
          volunteering: false,
        },
      ],
    });
    const w = mount(HiveWantedRequestsView);
    await flushPromises();
    expect(w.text()).toContain("Je m'en occupe");
    expect(w.get("[data-testid=hive-wanted-list]").text()).toContain("Le Miel d'Hiver");
    expect(w.text()).toContain("demandeurs anonymes");
  });
});
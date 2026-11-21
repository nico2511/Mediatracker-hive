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

import HiveOthersRequestsView from "./HiveOthersRequestsView.vue";

beforeEach(() => {
  apiMock.mockReset();
  patchMock.mockReset();
});

describe("HiveOthersRequestsView", () => {
  it("regroupe les sections et les interrupteurs", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/settings") {
        return {
          settings: [
            { key: "hive_auto_accept_requests", value: true },
            { key: "hive_auto_take_wanted", value: false },
          ],
        };
      }
      if (url === "/api/v1/hive/requests/received") {
        return {
          items: [
            {
              request_id: "req-demo-01",
              work_key: "wk1",
              title: "Film exemple",
              type: "film",
              season: null,
              episode: null,
              already_shared: false,
              status: "pending",
            },
          ],
        };
      }
      if (url === "/api/v1/hive/requests/wanted") {
        return {
          items: [
            {
              work_key: "wk2",
              title: "Série exemple",
              year: 2098,
              type: "series",
              season: 1,
              episode: null,
              requester_count: 2,
              volunteering: true,
            },
          ],
        };
      }
      throw new Error(url);
    });

    const w = mount(HiveOthersRequestsView);
    await flushPromises();

    expect(w.text()).toContain("Accepter automatiquement les demandes de partage");
    expect(w.text()).toContain("Prendre en charge automatiquement les recherches");
    expect(w.text()).toContain("Vous l’avez");
    expect(w.text()).toContain("Personne ne l’a encore");
    expect(w.get("[data-testid=hive-received-list]").text()).toContain("Film exemple");
    expect(w.get("[data-testid=hive-wanted-list]").text()).toContain("Série exemple");
    expect(w.text()).toContain("Le partage démarrera sans action supplémentaire");
    expect(w.text()).toContain("Ne plus m'en occuper");
  });

  it("enregistre hive_auto_take_wanted sans recharger les listes", async () => {
    let wantedCalls = 0;
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/settings") {
        return { settings: [{ key: "hive_auto_take_wanted", value: false }] };
      }
      if (url === "/api/v1/hive/requests/received") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/requests/wanted") {
        wantedCalls += 1;
        return { items: [] };
      }
      throw new Error(url);
    });
    patchMock.mockResolvedValue({});

    const w = mount(HiveOthersRequestsView);
    await flushPromises();
    expect(wantedCalls).toBe(1);

    await w.get("[data-testid=hive-auto-take-wanted-inline]").setValue(true);
    await flushPromises();

    expect(patchMock).toHaveBeenCalledWith({ values: { hive_auto_take_wanted: true } });
    expect(wantedCalls).toBe(1);
  });

  it("recharge les listes après un PATCH auto-accept réussi", async () => {
    let receivedCalls = 0;
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/settings") {
        return { settings: [{ key: "hive_auto_accept_requests", value: false }] };
      }
      if (url === "/api/v1/hive/requests/received") {
        receivedCalls += 1;
        return { items: [] };
      }
      if (url === "/api/v1/hive/requests/wanted") {
        return { items: [] };
      }
      throw new Error(url);
    });
    patchMock.mockResolvedValue({});

    const w = mount(HiveOthersRequestsView);
    await flushPromises();
    expect(receivedCalls).toBe(1);

    await w.get("[data-testid=hive-auto-accept-inline]").setValue(true);
    await flushPromises();

    expect(patchMock).toHaveBeenCalledWith({ values: { hive_auto_accept_requests: true } });
    expect(receivedCalls).toBe(2);
  });

  it("recharge les deux listes via reload", async () => {
    let receivedCalls = 0;
    let wantedCalls = 0;
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/settings") {
        return { settings: [] };
      }
      if (url === "/api/v1/hive/requests/received") {
        receivedCalls += 1;
        return { items: [] };
      }
      if (url === "/api/v1/hive/requests/wanted") {
        wantedCalls += 1;
        return { items: [] };
      }
      throw new Error(url);
    });

    const w = mount(HiveOthersRequestsView);
    await flushPromises();
    expect(receivedCalls).toBe(1);
    expect(wantedCalls).toBe(1);

    await (w.vm as unknown as { reload: () => Promise<void> }).reload();
    await flushPromises();
    expect(receivedCalls).toBe(2);
    expect(wantedCalls).toBe(2);
  });

  it("affiche le suivi factuel dès l’acceptation, avec la version courte sur téléphone", async () => {
    let receivedCalls = 0;
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/settings") {
        return { settings: [] };
      }
      if (url === "/api/v1/hive/requests/received/accept") {
        return undefined;
      }
      if (url === "/api/v1/hive/requests/received") {
        receivedCalls += 1;
        const status = receivedCalls === 1 ? "pending" : "sharing";
        return {
          items: [
            {
              request_id: "req-demo-01",
              work_key: "wk1",
              title: "Film exemple",
              type: "film",
              season: null,
              episode: null,
              already_shared: receivedCalls > 1,
              status,
            },
          ],
        };
      }
      if (url === "/api/v1/hive/requests/wanted") {
        return { items: [] };
      }
      throw new Error(url);
    });

    const w = mount(HiveOthersRequestsView);
    await flushPromises();
    await w.get("[data-testid=hive-accept-request]").trigger("click");
    await flushPromises();

    expect(w.get("p.ok .note-phone").text()).toBe("Partage en cours.");
    expect(w.get("p.ok .note-desktop").text()).toBe(
      "Partage en cours : synchronisation du catalogue local.",
    );
    expect(w.get("[data-testid=hive-received-follow-up]").text()).toContain("Partage en cours");
  });
});

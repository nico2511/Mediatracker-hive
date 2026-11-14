import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});

import AllDebridAccountBlock from "./AllDebridAccountBlock.vue";

function disconnected() {
  return {
    connected: false,
    source: "none" as const,
    env_locked: false,
    pin_phase: "idle" as const,
    pin: null,
    pin_message: null,
    pin_error: null,
    env_notice: null,
  };
}

beforeEach(() => {
  apiMock.mockReset();
});

describe("AllDebridAccountBlock", () => {
  it("affiche non connecté avec bouton PIN", async () => {
    apiMock.mockResolvedValueOnce(disconnected());
    const w = mount(AllDebridAccountBlock);
    await flushPromises();
    expect(w.find("[data-testid=alldebrid-start-pin]").exists()).toBe(true);
    expect(w.text()).toContain("Non connecté");
  });

  it("affiche connecté et déconnecter", async () => {
    apiMock.mockResolvedValueOnce({
      ...disconnected(),
      connected: true,
      source: "db",
    });
    const w = mount(AllDebridAccountBlock);
    await flushPromises();
    expect(w.find("[data-testid=alldebrid-disconnect]").exists()).toBe(true);
  });

  it("affiche le code PIN en attente", async () => {
    apiMock.mockResolvedValueOnce({
      ...disconnected(),
      pin_phase: "pending",
      pin: { pin: "ABCD", user_url: "https://alldebrid.com/pin/?pin=ABCD", expires_in: 120 },
    });
    apiMock.mockResolvedValue({
      ...disconnected(),
      pin_phase: "pending",
      pin: { pin: "ABCD", user_url: "https://alldebrid.com/pin/?pin=ABCD", expires_in: 118 },
    });
    const w = mount(AllDebridAccountBlock);
    await flushPromises();
    expect(w.find("[data-testid=alldebrid-pin-display]").text()).toContain("A B C D");
  });

  it("affiche l'état expiré", async () => {
    apiMock.mockResolvedValueOnce({
      ...disconnected(),
      pin_phase: "expired",
      pin_message: "Le code a expiré. Aucune clé n'a été enregistrée.",
    });
    const w = mount(AllDebridAccountBlock);
    await flushPromises();
    expect(w.find("[data-testid=alldebrid-retry-pin]").exists()).toBe(true);
    expect(w.text()).toContain("expiré");
  });

  it("recharge le compte à l’expiration du compte à rebours", async () => {
    vi.useFakeTimers();
    let accountLoads = 0;
    const pending = {
      ...disconnected(),
      pin_phase: "pending" as const,
      pin: { pin: "ABCD", user_url: "https://alldebrid.com/pin/?pin=ABCD", expires_in: 1 },
    };
    const expired = {
      ...disconnected(),
      pin_phase: "expired" as const,
      pin: null,
      pin_message: "Le code a expiré. Aucune clé n'a été enregistrée.",
    };
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        accountLoads += 1;
        return accountLoads === 1 ? pending : expired;
      }
      if (url.includes("/pin/poll")) {
        return pending;
      }
      return {};
    });
    const w = mount(AllDebridAccountBlock);
    await flushPromises();
    await vi.advanceTimersByTimeAsync(2000);
    await flushPromises();
    expect(accountLoads).toBeGreaterThanOrEqual(2);
    expect(w.find("[data-testid=alldebrid-retry-pin]").exists()).toBe(true);
    vi.useRealTimers();
  });

  it("masque PIN quand env_locked", async () => {
    apiMock.mockResolvedValueOnce({
      connected: true,
      source: "env",
      env_locked: true,
      pin_phase: "idle",
      pin: null,
      pin_message: null,
      pin_error: null,
      env_notice: "Clé définie dans la configuration du serveur.",
    });
    const w = mount(AllDebridAccountBlock);
    await flushPromises();
    expect(w.find("[data-testid=alldebrid-start-pin]").exists()).toBe(false);
    expect(w.find("[data-testid=alldebrid-disconnect]").exists()).toBe(false);
    expect(w.text()).toContain("configuration du serveur");
  });
});

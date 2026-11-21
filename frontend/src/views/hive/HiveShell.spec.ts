import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

function callsFor(url: string): number {
  return apiMock.mock.calls.filter((item) => item[0] === url).length;
}

function mockHiveApis(overview: Record<string, unknown>) {
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/hive/overview") {
      return overview;
    }
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
}

let wrapper: VueWrapper | undefined;

beforeEach(() => {
  apiMock.mockReset();
  mockHiveApis({
    enabled: true,
    next_gossip_at: "2099-01-01T12:10:00Z",
    last_gossip_at: "2099-01-01T12:00:00Z",
  });
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = undefined;
  vi.useRealTimers();
});

describe("HiveShell", () => {
  it("affiche les onglets Hive dont Demandes des autres", async () => {
    wrapper = mount(HiveShell);
    await flushPromises();
    expect(wrapper.get("[data-testid=hive-tab-catalogue]").text()).toContain("Catalogue");
    expect(wrapper.get("[data-testid=hive-tab-demandes]").text()).toContain("Mes demandes");
    expect(wrapper.get("[data-testid=hive-tab-demandes]").text()).toContain("2");
    expect(wrapper.get("[data-testid=hive-tab-demandes-autres]").text()).toContain("Demandes des autres");
    expect(wrapper.get("[data-testid=hive-tab-demandes-autres]").text()).toContain("3");
    expect(wrapper.get("[data-testid=hive-gossip-sync]").exists()).toBe(true);
    expect(wrapper.get("[data-testid=hive-gossip-countdown]").text()).toMatch(/dans \d+ min/);
  });

  it("masque la barre si Hive est désactivée", async () => {
    mockHiveApis({
      enabled: false,
      next_gossip_at: "2099-01-01T12:10:00Z",
      last_gossip_at: "2099-01-01T12:00:00Z",
    });
    wrapper = mount(HiveShell);
    await flushPromises();
    expect(wrapper.find("[data-testid=hive-gossip-sync]").exists()).toBe(false);
  });

  it("masque la barre si next_gossip_at est nul", async () => {
    mockHiveApis({
      enabled: true,
      next_gossip_at: null,
      last_gossip_at: null,
    });
    wrapper = mount(HiveShell);
    await flushPromises();
    expect(wrapper.find("[data-testid=hive-gossip-sync]").exists()).toBe(false);
  });

  it("ne relance pas les requêtes si l’échéance est déjà passée au montage", async () => {
    vi.useFakeTimers();
    const now = 1_800_000_000_000;
    vi.setSystemTime(now);
    const past = new Date(now - 60_000).toISOString();
    const last = new Date(now - 600_000).toISOString();
    mockHiveApis({
      enabled: true,
      next_gossip_at: past,
      last_gossip_at: last,
    });
    wrapper = mount(HiveShell);
    await flushPromises();
    const initialOverview = callsFor("/api/v1/hive/overview");
    const initialReceived = callsFor("/api/v1/hive/requests/received");
    expect(initialOverview).toBe(1);
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview);
    expect(callsFor("/api/v1/hive/requests/received")).toBe(initialReceived);
  });

  it("recharge une seule fois à l’échéance même si next_gossip_at reste inchangé", async () => {
    vi.useFakeTimers();
    const now = 1_800_000_000_000;
    vi.setSystemTime(now);
    const next = new Date(now + 3_000).toISOString();
    const last = new Date(now - 600_000).toISOString();
    mockHiveApis({
      enabled: true,
      next_gossip_at: next,
      last_gossip_at: last,
    });
    wrapper = mount(HiveShell);
    await flushPromises();
    const initialOverview = callsFor("/api/v1/hive/overview");
    const initialMine = callsFor("/api/v1/hive/requests/mine");
    const initialReceived = callsFor("/api/v1/hive/requests/received");
    const initialWanted = callsFor("/api/v1/hive/requests/wanted");
    expect(initialOverview).toBe(1);
    await vi.advanceTimersByTimeAsync(2_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview);
    await vi.advanceTimersByTimeAsync(2_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 1);
    expect(callsFor("/api/v1/hive/requests/mine")).toBe(initialMine + 1);
    expect(callsFor("/api/v1/hive/requests/received")).toBe(initialReceived + 1);
    expect(callsFor("/api/v1/hive/requests/wanted")).toBe(initialWanted + 1);
    await vi.advanceTimersByTimeAsync(10_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 1);
    expect(callsFor("/api/v1/hive/requests/received")).toBe(initialReceived + 1);
    expect(callsFor("/api/v1/hive/requests/wanted")).toBe(initialWanted + 1);
  });

  it("programme un seul nouvel essai 60 s plus tard si next reste inchangé", async () => {
    vi.useFakeTimers();
    const now = 1_800_000_000_000;
    vi.setSystemTime(now);
    const next = new Date(now + 3_000).toISOString();
    const last = new Date(now - 600_000).toISOString();
    mockHiveApis({
      enabled: true,
      next_gossip_at: next,
      last_gossip_at: last,
    });
    wrapper = mount(HiveShell);
    await flushPromises();
    const initialOverview = callsFor("/api/v1/hive/overview");
    const initialReceived = callsFor("/api/v1/hive/requests/received");
    const initialWanted = callsFor("/api/v1/hive/requests/wanted");

    await vi.advanceTimersByTimeAsync(3_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 1);
    expect(callsFor("/api/v1/hive/requests/received")).toBe(initialReceived + 1);
    expect(callsFor("/api/v1/hive/requests/wanted")).toBe(initialWanted + 1);

    await vi.advanceTimersByTimeAsync(59_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 1);

    await vi.advanceTimersByTimeAsync(1_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 2);
    expect(callsFor("/api/v1/hive/requests/received")).toBe(initialReceived + 2);
    expect(callsFor("/api/v1/hive/requests/wanted")).toBe(initialWanted + 2);

    await vi.advanceTimersByTimeAsync(120_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 2);
    expect(callsFor("/api/v1/hive/requests/received")).toBe(initialReceived + 2);
    expect(callsFor("/api/v1/hive/requests/wanted")).toBe(initialWanted + 2);
  });

  it("n’exécute pas l’essai de 60 s après démontage", async () => {
    vi.useFakeTimers();
    const now = 1_800_000_000_000;
    vi.setSystemTime(now);
    const next = new Date(now + 3_000).toISOString();
    const last = new Date(now - 600_000).toISOString();
    mockHiveApis({
      enabled: true,
      next_gossip_at: next,
      last_gossip_at: last,
    });
    wrapper = mount(HiveShell);
    await flushPromises();
    const initialOverview = callsFor("/api/v1/hive/overview");

    await vi.advanceTimersByTimeAsync(4_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 1);

    wrapper.unmount();
    wrapper = undefined;
    expect(vi.getTimerCount()).toBe(0);

    await vi.advanceTimersByTimeAsync(120_000);
    await flushPromises();
    expect(callsFor("/api/v1/hive/overview")).toBe(initialOverview + 1);
  });

  it("nettoie le timer au démontage", async () => {
    vi.useFakeTimers();
    wrapper = mount(HiveShell);
    await flushPromises();
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    wrapper.unmount();
    wrapper = undefined;
    expect(vi.getTimerCount()).toBe(0);
  });
});

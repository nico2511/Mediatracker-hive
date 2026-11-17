import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import SettingsSectionView from "./views/settings/SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("./api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  userFacingApiError: (_e: unknown, f: string) => f,
  isNetworkError: () => false,
  isTransientHttpStatus: () => false,
}));
vi.mock("./settings", () => ({
  fetchSettings: fetchSettingsMock,
  patchSettings: patchSettingsMock,
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { section: "qualite" } }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));
vi.mock("./pwa/pwaInstall", () => ({
  openInstallPanel: vi.fn(),
  usePwaInstall: () => ({ deferred: null }),
}));

const profiles = [
  {
    id: 1,
    name: "Compact",
    languages: ["VF"],
    min_resolution: "HD",
    max_resolution: "HD",
    min_size_mb: null,
    max_size_mb: 2048,
    exclude_keywords: [],
    is_default: true,
  },
  {
    id: 2,
    name: "Souple",
    languages: ["VF", "MULTI"],
    min_resolution: "HD",
    max_resolution: "1080p",
    min_size_mb: null,
    max_size_mb: 6144,
    exclude_keywords: [],
    is_default: false,
  },
];

describe("Profils de repli — réglages", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    patchSettingsMock.mockReset();
    fetchSettingsMock.mockReset();
    apiMock.mockReset();
  });

  it("retrait puis sauvegarde envoie l’ordre mis à jour", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "allow_fallback", value: true },
        { key: "profile_fallback_order", value: "1,2" },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/quality-profiles") {
        return { profiles };
      }
      return {};
    });
    patchSettingsMock.mockResolvedValue(undefined);
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Ordre global des profils");
    const removeButtons = wrapper.findAll(".profile-fallback-actions button").filter((b) => b.text() === "Retirer");
    await removeButtons.at(1)?.trigger("click");
    await wrapper.find("form.profile-fallback-panel").trigger("submit.prevent");
    await flushPromises();
    const last = patchSettingsMock.mock.calls.at(-1)?.[0] as { values: Record<string, unknown> };
    expect(last.values.allow_fallback).toBe(true);
    expect(last.values.profile_fallback_order).toBe("1");
  });

  it("échec de sauvegarde ne vide pas l’ordre local", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "allow_fallback", value: false },
        { key: "profile_fallback_order", value: "2" },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/quality-profiles") {
        return { profiles };
      }
      return {};
    });
    patchSettingsMock.mockRejectedValue(new Error("refus"));
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Souple");
    await wrapper.find("form.profile-fallback-panel").trigger("submit.prevent");
    await flushPromises();
    expect(wrapper.text()).toContain("Souple");
    expect(patchSettingsMock).toHaveBeenCalled();
  });

  it("GET settings rejeté : pas de formulaire ni PATCH", async () => {
    fetchSettingsMock.mockRejectedValue(new Error("refus réseau"));
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/quality-profiles") {
        return { profiles };
      }
      return {};
    });
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
      },
    });
    await flushPromises();
    expect(wrapper.find("form.profile-fallback-panel").exists()).toBe(false);
    expect(wrapper.text()).toContain("Chargement impossible");
    expect(patchSettingsMock).not.toHaveBeenCalled();
  });

  it("GET settings retardé : pas de PATCH avant chargement", async () => {
    let resolvePanelSettings: (value: unknown) => void = () => undefined;
    let settingsFetches = 0;
    let panelSettingsPromise: Promise<{ settings: unknown[] }> | null = null;
    fetchSettingsMock.mockImplementation(async () => {
      settingsFetches += 1;
      if (settingsFetches === 1) {
        return { settings: [] };
      }
      if (!panelSettingsPromise) {
        panelSettingsPromise = new Promise((resolve) => {
          resolvePanelSettings = (value: unknown) =>
            resolve(value as { settings: unknown[] });
        });
      }
      return panelSettingsPromise;
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/quality-profiles") {
        return { profiles };
      }
      return {};
    });
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
      },
    });
    await flushPromises();
    expect(wrapper.find("form.profile-fallback-panel").exists()).toBe(false);
    expect(settingsFetches).toBeGreaterThanOrEqual(2);
    resolvePanelSettings({
      settings: [
        { key: "allow_fallback", value: true },
        { key: "profile_fallback_order", value: "1,2" },
      ],
    });
    await flushPromises();
    await flushPromises();
    expect(wrapper.find("form.profile-fallback-panel").exists()).toBe(true);
    expect(patchSettingsMock).not.toHaveBeenCalled();
  });

  it("liste volontairement vide après chargement : sauvegarde autorisée", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "allow_fallback", value: false },
        { key: "profile_fallback_order", value: "" },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/quality-profiles") {
        return { profiles };
      }
      return {};
    });
    patchSettingsMock.mockResolvedValue(undefined);
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
      },
    });
    await flushPromises();
    await wrapper.find("form.profile-fallback-panel").trigger("submit.prevent");
    await flushPromises();
    const last = patchSettingsMock.mock.calls.at(-1)?.[0] as { values: Record<string, unknown> };
    expect(last.values.profile_fallback_order).toBe("");
    expect(last.values.allow_fallback).toBe(false);
  });
});

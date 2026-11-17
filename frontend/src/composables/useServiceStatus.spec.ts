import { beforeEach, describe, expect, it, vi } from "vitest";

import { refreshServiceStatus, resetServiceStatusForTests, serviceStatusRefs } from "./useServiceStatus";

const apiMock = vi.fn();
const fetchSettingsMock = vi.fn();

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
}));

vi.mock("../settings", () => ({
  fetchSettings: () => fetchSettingsMock(),
}));

describe("refreshServiceStatus", () => {
  beforeEach(() => {
    apiMock.mockReset();
    fetchSettingsMock.mockReset();
    resetServiceStatusForTests();
  });

  it("met en cache adConnected, downloadReady et les drapeaux réglages", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: true };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          download_ready: false,
          sources: [
            { source: "wawacity", available: false },
            { source: "hive", available: true },
          ],
        };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: true };
      }
      return {};
    });
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "jellyfin_url", value: "https://jellyfin.example", secret: false, set: true },
        { key: "jellyfin_api_key", value: null, secret: true, set: true },
        { key: "hive_enabled", value: true, secret: false, set: true },
        { key: "tmdb_read_token", value: null, secret: true, set: true },
      ],
    });

    const refs = serviceStatusRefs();
    await refreshServiceStatus();

    expect(refs.adConnected.value).toBe(true);
    expect(refs.downloadReady.value).toBe(false);
    expect(refs.sourceAvailable.value.wawacity).toBe(false);
    expect(refs.sourceAvailable.value.hive).toBe(true);
    expect(refs.jellyfinConfigured.value).toBe(true);
    expect(refs.hiveEnabled.value).toBe(true);
    expect(refs.tmdbSet.value).toBe(true);
    expect(refs.watchtowerConfigured.value).toBe(true);
    expect(refs.serviceStatusLoaded.value).toBe(true);
  });

  it("lit watchtower_configured depuis /api/v1/ha/update", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: true };
      }
      if (url === "/api/v1/settings/sources/status") {
        return { download_ready: true, sources: [] };
      }
      return {};
    });
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "watchtower_url", value: "", secret: false, set: false },
        { key: "watchtower_token", value: null, secret: true, set: false },
      ],
    });
    await refreshServiceStatus();
    expect(serviceStatusRefs().watchtowerConfigured.value).toBe(true);
  });

  it("relit les réglages à chaque refresh", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false };
      }
      if (url === "/api/v1/settings/sources/status") {
        return { download_ready: true, sources: [] };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: false };
      }
      return {};
    });
    fetchSettingsMock.mockResolvedValue({ settings: [] });
    await refreshServiceStatus();
    await refreshServiceStatus();
    expect(fetchSettingsMock).toHaveBeenCalledTimes(2);
  });
});
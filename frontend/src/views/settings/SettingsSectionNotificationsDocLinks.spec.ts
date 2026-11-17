import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests } from "../../composables/useServiceStatus";
import SettingsSectionView from "./SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  userFacingApiError: (_e: unknown, f: string) => f,
  isNetworkError: () => false,
  isTransientHttpStatus: () => false,
}));
vi.mock("../../settings", () => ({
  fetchSettings: fetchSettingsMock,
  patchSettings: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { section: "notifications" } }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));
vi.mock("../../pwa/pwaInstall", () => ({
  openInstallPanel: vi.fn(),
  usePwaInstall: () => ({ deferred: null }),
}));

describe("Réglages notifications — liens de doc", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetServiceStatusForTests();
    fetchSettingsMock.mockResolvedValue({ settings: [] });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: true };
      }
      if (url === "/api/v1/settings/sources/status") {
        return { sources: [], download_ready: true };
      }
      if (url === "/api/v1/webhooks") {
        return [];
      }
      if (url === "/api/v1/events/notifications") {
        return { events: [] };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: true };
      }
      return {};
    });
  });

  it("affiche le lien Déclencheur webhook pour Home Assistant", async () => {
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
      },
    });
    await flushPromises();
    const ha = wrapper.find("[data-testid=ha-webhook-trigger-doc]");
    expect(ha.attributes("href")).toContain("home-assistant.io");
    expect(ha.text()).toBe("Déclencheur webhook ↗");
  });
});
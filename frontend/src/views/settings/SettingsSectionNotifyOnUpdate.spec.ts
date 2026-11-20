import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests } from "../../composables/useServiceStatus";
import SettingsSectionView from "./SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  userFacingApiError: (_e: unknown, f: string) => f,
  isNetworkError: () => false,
  isTransientHttpStatus: () => false,
}));
vi.mock("../../settings", () => ({
  fetchSettings: fetchSettingsMock,
  patchSettings: patchSettingsMock,
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { section: "maj" } }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));
vi.mock("../../pwa/pwaInstall", () => ({
  openInstallPanel: vi.fn(),
  usePwaInstall: () => ({ deferred: null }),
}));

const NOTIFY_LABEL = "Me notifier quand une nouvelle version démarre";

function mountMajSection() {
  return mount(SettingsSectionView, {
    global: {
      stubs: {
        DatabaseBackupRestore: true,
        DiskSpaceCard: true,
        SourceRank: true,
        AllDebridAccountBlock: true,
        RouterLink: true,
      },
    },
  });
}

describe("Réglages maj — notification de nouvelle version", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetServiceStatusForTests();
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "notify_on_update", value: false, secret: false, set: true }],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/ha/update") {
        return {
          watchtower_configured: false,
          version: "2.0.0",
          active_downloads: 0,
          detail: "ok",
          update_available: false,
        };
      }
      if (url === "/api/v1/webhooks") {
        return [];
      }
      if (url === "/api/v1/events/notifications") {
        return { events: [] };
      }
      return {};
    });
  });

  it("affiche l'interrupteur même sans Watchtower configuré", async () => {
    const wrapper = mountMajSection();
    await flushPromises();
    expect(wrapper.text()).toContain(NOTIFY_LABEL);
    expect(wrapper.text()).not.toContain("Me notifier quand Watchtower");
    const checkbox = wrapper.find('.settings-setrow input[type="checkbox"]');
    expect(checkbox.exists()).toBe(true);
  });

  it("affiche l'aide quand aucune cible de notification n'est active", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/ha/update") {
        return {
          watchtower_configured: true,
          version: "2.0.0",
          active_downloads: 0,
          detail: "ok",
          update_available: false,
        };
      }
      if (url === "/api/v1/webhooks") {
        return [{ id: 1, name: "HA", url: "http://ha", type: "homeassistant", events: [], enabled: false, failure_count: 0 }];
      }
      if (url === "/api/v1/events/notifications") {
        return { events: [] };
      }
      return {};
    });
    const wrapper = mountMajSection();
    await flushPromises();
    const hint = wrapper.find('[data-testid="notify-on-update-no-target-hint"]');
    expect(hint.exists()).toBe(true);
    expect(hint.text()).toBe("Aucune cible de notification active");
  });
});
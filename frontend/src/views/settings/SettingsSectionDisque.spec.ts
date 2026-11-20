import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests } from "../../composables/useServiceStatus";
import SettingsSectionView from "./SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());
const routeSection = vi.hoisted(() => ({ value: "disque" as string }));

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
  useRoute: () => ({ params: { section: routeSection.value } }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));
vi.mock("../../pwa/pwaInstall", () => ({
  openInstallPanel: vi.fn(),
  usePwaInstall: () => ({ deferred: null }),
}));

function serviceStatusDefaults(url: string) {
  if (url === "/api/v1/integrations/alldebrid/account") {
    return { connected: true };
  }
  if (url === "/api/v1/settings/sources/status") {
    return { sources: [], download_ready: true };
  }
  if (url === "/api/v1/ha/update") {
    return { watchtower_configured: true };
  }
  if (url === "/api/v1/health/disk") {
    return {
      computed_at: "2026-10-20T12:00:00+00:00",
      running: false,
      error: "",
      free_bytes: 1_000_000,
      folders: [
        {
          key: "incomplete",
          label: "Téléchargements incomplets",
          path: "/data/incomplete",
          bytes: 0,
          missing: false,
          device: "",
          mount: "/data",
          files: [],
          incomplete_trash_count: 0,
          incomplete_kept_recent_count: 0,
          incomplete_keep_until: null,
        },
      ],
      volumes: [],
    };
  }
  if (url.startsWith("/api/v1/settings/storage")) {
    return {
      media_dir: "/data",
      movies_dir: "/data/films",
      series_dir: "/data/series",
      downloads_dir: "/data/incomplete",
      trash_dir: "/data/.trash",
      backups_dir: "/config/backups",
      warning: "",
    };
  }
  return null;
}

describe("Réglages — section Disque et sauvegardes", () => {
  beforeEach(() => {
    resetServiceStatusForTests();
    routeSection.value = "disque";
    apiMock.mockReset();
    fetchSettingsMock.mockReset();
    patchSettingsMock.mockReset();
    apiMock.mockImplementation((url: string) => {
      const body = serviceStatusDefaults(url);
      if (body === null) {
        return Promise.reject(new Error(`unexpected ${url}`));
      }
      return Promise.resolve(body);
    });
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "disk_min_free_gb", value: 5 },
        { key: "backup_keep", value: 14 },
      ],
    });
  });

  it("structure la page en trois sous-sections", async () => {
    const wrapper = mount(SettingsSectionView, {
      global: {
        stubs: { DatabaseBackupRestore: true, SourceRank: true },
      },
    });
    await flushPromises();
    const section = wrapper.find("[data-testid='settings-disque-section']");
    expect(section.exists()).toBe(true);
    const titles = section.findAll(".settings-subsection-title").map((node) => node.text());
    expect(titles).toEqual([
      "Espace disque",
      "Téléchargements incomplets et orphelins",
      "Sauvegardes",
    ]);
    expect(section.find("[data-disk-folders-details]").exists()).toBe(true);
    expect(section.find("[data-disk-panel='disk']").exists()).toBe(true);
    expect(section.find("[data-disk-panel='incomplete']").exists()).toBe(true);
  });
});
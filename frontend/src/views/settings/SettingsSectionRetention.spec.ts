import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests } from "../../composables/useServiceStatus";
import SettingsSectionView from "./SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());
const routeSection = vi.hoisted(() => ({ value: "jellyfin" }));

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
  return null;
}

function mountSettings() {
  return mount(SettingsSectionView, {
    global: {
      stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
    },
  });
}

describe("Réglages — libellés dynamiques", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetServiceStatusForTests();
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusDefaults(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/quality-profiles") {
        return { profiles: [] };
      }
      if (url === "/api/v1/trash") {
        return { items: [], trash_bytes: 0, reclaimed_bytes: 0 };
      }
      if (url === "/api/v1/storage") {
        return {
          media_dir: "/data/media",
          movies_dir: "/data/m",
          series_dir: "/data/s",
          downloads_dir: "/data/d",
          trash_dir: "/data/t",
          backups_dir: "/custom/volume/backups",
          warning: null,
        };
      }
      if (url === "/api/v1/integrations/jellyfin") {
        return {
          webhook_url: null,
          webhook_token_set: false,
          webhook_token_preview: null,
          webhook_token_header: "X-MT-Token",
          last_sync_at: null,
          last_sync_error: null,
        };
      }
      return {};
    });
  });

  it("propose des liens de doc sur les chemins et le webhook Jellyfin", async () => {
    routeSection.value = "jellyfin";
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "jellyfin_poll_interval_min", value: 30 },
        { key: "jellyfin_url", value: "http://jellyfin:8096" },
        { key: "jellyfin_api_key", value: "", set: true },
      ],
    });
    const wrapper = mountSettings();
    await flushPromises();
    expect(wrapper.find("[data-testid=jellyfin-doc-movies]").attributes("href")).toBe(
      "https://jellyfin.org/docs/general/server/media/movies/",
    );
    expect(wrapper.find("[data-testid=jellyfin-doc-shows]").attributes("href")).toBe(
      "https://jellyfin.org/docs/general/server/media/shows/",
    );
    expect(wrapper.find("[data-testid=jellyfin-doc-webhook-plugin]").attributes("href")).toBe(
      "https://github.com/jellyfin/jellyfin-plugin-webhook",
    );
    expect(wrapper.find("[data-testid=jellyfin-doc-plugins]").attributes("href")).toBe(
      "https://jellyfin.org/docs/general/server/plugins/",
    );
  });

  it("affiche l’intervalle Jellyfin configuré", async () => {
    routeSection.value = "jellyfin";
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "jellyfin_poll_interval_min", value: 45 },
        { key: "jellyfin_url", value: "http://jellyfin:8096" },
        { key: "jellyfin_api_key", value: "", set: true },
      ],
    });
    const wrapper = mountSettings();
    await flushPromises();
    expect(wrapper.text()).toContain("45 minutes");
    expect(wrapper.text()).not.toContain("30 minutes");
  });

  it("affiche le chemin des sauvegardes renvoyé par l’API", async () => {
    routeSection.value = "disque";
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "disk_min_free_gb", value: 5 }],
    });
    const wrapper = mountSettings();
    await flushPromises();
    expect(wrapper.text()).toContain("/custom/volume/backups");
    expect(wrapper.text()).not.toContain("/config/backups");
  });

  it("libelle l’espace libre minimum en Gio", async () => {
    routeSection.value = "disque";
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "disk_min_free_gb", value: 20 }],
    });
    const wrapper = mountSettings();
    await flushPromises();
    expect(wrapper.text()).toContain("Espace libre minimum (Gio)");
    expect(wrapper.text()).not.toContain("Espace libre minimum (Go)");
  });

  it("ne propose plus backup_keep dans Disque ni trash_dir dans Scan", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "disk_min_free_gb", value: 5 },
        { key: "backup_keep", value: 7 },
        { key: "trash_dir", value: "/data/.trash" },
        { key: "downloads_completed_action", value: "archive" },
        { key: "scan_interval_min", value: 120 },
        { key: "library_scan_interval_hours", value: 24 },
      ],
    });
    routeSection.value = "disque";
    const disque = mountSettings();
    await flushPromises();
    expect(disque.text()).toContain("voir Corbeille");
    expect(disque.text()).not.toContain("Sauvegardes conservées");
    routeSection.value = "scan";
    const scan = mountSettings();
    await flushPromises();
    const scanLabels = scan.findAll("label span").map((node) => node.text());
    expect(scanLabels).not.toContain("Corbeille");
    expect(scanLabels).not.toContain("Après téléchargement");
    expect(scan.text()).toContain("Corbeille");
  });
});
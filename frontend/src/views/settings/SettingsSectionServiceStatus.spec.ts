import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests, serviceStatusRefs } from "../../composables/useServiceStatus";
import SettingsSectionView from "./SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());
const routeSection = vi.hoisted(() => ({ value: "alldebrid" }));

const serviceFlags = vi.hoisted(() => ({
  adConnected: true,
  downloadReady: true,
  jellyfinConfigured: true,
  watchtowerConfigured: true,
}));

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

function serviceStatusHandler(url: string) {
  if (url === "/api/v1/integrations/alldebrid/account") {
    return { connected: serviceFlags.adConnected };
  }
  if (url === "/api/v1/settings/sources/status") {
    return { sources: [], download_ready: serviceFlags.downloadReady };
  }
  if (url === "/api/v1/ha/update") {
    return { watchtower_configured: serviceFlags.watchtowerConfigured };
  }
  return null;
}

function mountSection() {
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

describe("SettingsSectionView — statut des services", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetServiceStatusForTests();
    serviceFlags.adConnected = true;
    serviceFlags.downloadReady = true;
    serviceFlags.jellyfinConfigured = true;
    serviceFlags.watchtowerConfigured = true;
    fetchSettingsMock.mockResolvedValue({ settings: [] });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusHandler(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/quality-profiles") {
        return { profiles: [{ id: 1, name: "Démo", languages: ["VF"], max_resolution: "1080p", is_default: true }] };
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
      if (url.startsWith("/api/v1/integrations/jellyfin/users")) {
        return { users: [] };
      }
      return {};
    });
  });

  it("masque la purge AllDebrid sans connexion", async () => {
    routeSection.value = "alldebrid";
    serviceFlags.adConnected = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "alldebrid_auto_purge", value: true }],
    });
    const wrapper = mountSection();
    await flushPromises();
    expect(wrapper.text()).toContain("Disponible après connexion de AllDebrid");
    expect(wrapper.text()).not.toContain("Purger AllDebrid après");
    expect(wrapper.text()).not.toContain("Tout purger");
  });


  it("remplace les profils de qualité quand download_ready est faux", async () => {
    routeSection.value = "qualite";
    serviceFlags.downloadReady = false;
    const wrapper = mountSection();
    await flushPromises();
    expect(wrapper.text()).toContain("téléchargements sont prêts");
    expect(wrapper.text()).not.toContain("Modifier");
  });

  it("masque les champs scan liés au téléchargement", async () => {
    routeSection.value = "scan";
    serviceFlags.downloadReady = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "scan_interval_min", value: 120 },
        { key: "scan_ongoing_only", value: true },
        { key: "auto_download_default", value: true },
        { key: "library_scan_interval_hours", value: 24 },
      ],
    });
    const wrapper = mountSection();
    await flushPromises();
    const scanLabels = wrapper.findAll("label span").map((node) => node.text());
    expect(scanLabels).not.toContain("Intervalle de scan des sources (minutes)");
    expect(wrapper.text()).not.toContain("Séries en cours seulement");
    expect(wrapper.text()).not.toContain("Téléchargement automatique pour les nouveaux suivis");
    expect(wrapper.text()).toContain("Dossier médias");
    await wrapper.find("form").trigger("submit.prevent");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalled();
    const patch = patchSettingsMock.mock.calls.at(-1)?.[0] as { values: Record<string, unknown> };
    expect(patch.values.scan_ongoing_only).toBe(true);
    expect(patch.values.scan_interval_min).toBe(120);
  });

  it("garde Mettre à jour maintenant indisponible sans Watchtower et affiche la notification de version", async () => {
    routeSection.value = "maj";
    serviceFlags.watchtowerConfigured = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "watchtower_url", value: "" },
        { key: "notify_on_update", value: true },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusHandler(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/ha/update") {
        return {
          watchtower_configured: false,
          version: "0.0.0",
          active_downloads: 0,
          detail: "ok",
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
    const wrapper = mountSection();
    await flushPromises();
    expect(wrapper.text()).toContain("Indisponible : renseignez l'URL et le jeton Watchtower");
    expect(wrapper.text()).toContain("Me notifier quand une nouvelle version démarre");
    expect(wrapper.text()).toContain("Vérifier la mise à jour");
    expect(wrapper.text()).toContain("Redémarrer");
    expect(wrapper.text()).toContain("Mettre à jour maintenant");
  });

  it("masque les options Jellyfin et oriente vers API et jetons", async () => {
    routeSection.value = "jellyfin";
    serviceFlags.jellyfinConfigured = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "jellyfin_url", value: "" },
        { key: "jellyfin_api_key", value: "", set: false },
        { key: "jellyfin_user_id", value: "u-demo" },
        { key: "jellyfin_poll_interval_min", value: 30 },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusHandler(url);
      if (status !== null) {
        return status;
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
    const wrapper = mountSection();
    await vi.waitFor(() => expect(serviceStatusRefs().serviceStatusLoaded.value).toBe(true));
    await flushPromises();
    expect(wrapper.text()).toContain("Disponible après connexion de Jellyfin");
    expect(wrapper.text()).toContain("API et jetons");
    expect(wrapper.text()).not.toContain("URL Jellyfin");
    expect(wrapper.text()).not.toContain("Tester la connexion");
    expect(wrapper.text()).not.toContain("Utilisateur Jellyfin");
  });

  it("enregistre Jellyfin depuis la carte jetons puis rafraîchit le statut", async () => {
    routeSection.value = "jetons";
    serviceFlags.jellyfinConfigured = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "tmdb_read_token", value: null, secret: true, set: false },
        { key: "ghcr_read_token", value: null, secret: true, set: false },
        { key: "github_update_token", value: null, secret: true, set: false },
        { key: "watchtower_url", value: "", secret: false, set: false },
        { key: "watchtower_token", value: null, secret: true, set: false },
        { key: "jellyfin_url", value: "", secret: false, set: false },
        { key: "jellyfin_api_key", value: "", set: false },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      const status = serviceStatusHandler(url);
      if (status !== null) {
        return status;
      }
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false, pin_phase: "idle", source: "none", env_locked: false };
      }
      if (url === "/api/v1/tokens") {
        return { items: [] };
      }
      return {};
    });
    const fetchCountBefore = fetchSettingsMock.mock.calls.length;
    const wrapper = mountSection();
    await flushPromises();
    await wrapper.find("[data-testid=jellyfin-url-input]").setValue("http://jellyfin:8096");
    await wrapper.find("[data-testid=jellyfin-key-input]").setValue("secret-demo");
    await wrapper.find("[data-testid=jellyfin-secrets-form]").trigger("submit.prevent");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalledWith({
      values: { jellyfin_url: "http://jellyfin:8096", jellyfin_api_key: "secret-demo" },
    });
    expect(fetchSettingsMock.mock.calls.length).toBeGreaterThan(fetchCountBefore);
  });
});
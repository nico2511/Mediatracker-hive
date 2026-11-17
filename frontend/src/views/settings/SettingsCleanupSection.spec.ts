import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { resetServiceStatusForTests } from "../../composables/useServiceStatus";
import SettingsSectionView from "./SettingsSectionView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());
const routeSection = vi.hoisted(() => ({ value: "corbeille" }));

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

function mountCorbeille() {
  return mount(SettingsSectionView, {
    global: {
      stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
    },
  });
}

function defaultApi(url: string) {
  if (url === "/api/v1/integrations/alldebrid/account") {
    return { connected: true };
  }
  if (url === "/api/v1/settings/sources/status") {
    return { sources: [], download_ready: true };
  }
  if (url === "/api/v1/ha/update") {
    return { watchtower_configured: true };
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
      warning: null,
    };
  }
  if (url === "/api/v1/cleanup/migration-banner") {
    return { message: null };
  }
  if (url === "/api/v1/settings/fixed-retention") {
    return {
      event_journal_days: 30,
      source_state_days: 30,
      metrics_history_days: 7,
      incomplete_orphan_keep_days: 3,
    };
  }
  return {};
}

describe("Réglages — section Corbeille", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetServiceStatusForTests();
    routeSection.value = "corbeille";
    apiMock.mockImplementation(async (url: string) => defaultApi(url));
  });

  it("affiche les blocs de la page Corbeille", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "auto_delete_days", value: 14 },
        { key: "incomplete_cleanup_enabled", value: false },
      ],
    });
    const wrapper = mountCorbeille();
    await flushPromises();
    expect(wrapper.find("[data-testid=corbeille-bloc-contenu]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=corbeille-bloc-duree]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=corbeille-bloc-durees-fixes]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=corbeille-bloc-auto-delete]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=corbeille-bloc-incomplets]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=corbeille-bloc-conservation]").exists()).toBe(true);
    expect(wrapper.text()).toContain("Une fiche peut imposer son propre délai");
    expect(wrapper.find("[data-testid=corbeille-bloc-durees-fixes]").text()).toContain("Journal des événements");
    expect(wrapper.find("[data-testid=corbeille-bloc-durees-fixes]").text()).toContain("30 jours");
  });

  it("affiche la case cochée quand auto_delete_enabled est absent (défaut actif)", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "auto_delete_days", value: 14 },
        { key: "incomplete_cleanup_enabled", value: false },
      ],
    });
    const wrapper = mountCorbeille();
    await flushPromises();
    const boxes = wrapper.findAll('input[type="checkbox"]');
    const autoToggle = boxes.find((b) => b.element.parentElement?.textContent?.includes("Suppression automatique"));
    expect(autoToggle?.element).toBeInstanceOf(HTMLInputElement);
    expect((autoToggle!.element as HTMLInputElement).checked).toBe(true);
    const autoDeleteBlock = wrapper.find("[data-testid=corbeille-bloc-auto-delete]");
    const daysInput = autoDeleteBlock.find('input[type="number"]');
    expect((daysInput.element as HTMLInputElement).disabled).toBe(false);
  });

  it("grise le champ jours quand la suppression est désactivée", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "auto_delete_enabled", value: false },
        { key: "auto_delete_days", value: 14 },
        { key: "incomplete_cleanup_enabled", value: false },
      ],
    });
    const wrapper = mountCorbeille();
    await flushPromises();
    const autoDeleteBlock = wrapper.find("[data-testid=corbeille-bloc-auto-delete]");
    const daysInput = autoDeleteBlock.find('input[type="number"]');
    expect((daysInput.element as HTMLInputElement).disabled).toBe(true);
    expect(autoDeleteBlock.text()).toContain("Désactivée : aucun fichier ne partira");
    expect(autoDeleteBlock.find(".cleanup-days-muted").exists()).toBe(true);
  });

  it("affiche « Enregistré. » uniquement dans le bloc concerné", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "trash_retention_days", value: 7 },
        { key: "trash_dir", value: "/data/.trash" },
      ],
    });
    patchSettingsMock.mockResolvedValue({});
    const wrapper = mountCorbeille();
    await flushPromises();
    await wrapper.find("[data-testid=corbeille-bloc-duree] form").trigger("submit.prevent");
    await flushPromises();
    const duree = wrapper.find("[data-testid=corbeille-bloc-duree]");
    expect(duree.text()).toContain("Enregistré.");
    expect(wrapper.find("[data-testid=corbeille-bloc-incomplets]").text()).not.toContain("Enregistré.");
    expect(wrapper.find(".settings-section-title").text()).not.toContain("Enregistré.");
  });

  it("affiche le lien AllDebrid seulement si le compte est connecté", async () => {
    fetchSettingsMock.mockResolvedValue({ settings: [] });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false };
      }
      return defaultApi(url);
    });
    const wrapper = mountCorbeille();
    await flushPromises();
    expect(wrapper.find("[data-testid=corbeille-bloc-alldebrid]").exists()).toBe(false);
    apiMock.mockImplementation(async (url: string) => defaultApi(url));
    const wrapperConnected = mountCorbeille();
    await flushPromises();
    expect(wrapperConnected.find("[data-testid=corbeille-bloc-alldebrid]").exists()).toBe(true);
  });
});
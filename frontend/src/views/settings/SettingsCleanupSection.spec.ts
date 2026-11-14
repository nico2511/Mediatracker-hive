import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  useRoute: () => ({ params: { section: "nettoyage" } }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));
vi.mock("../../pwa/pwaInstall", () => ({
  openInstallPanel: vi.fn(),
  usePwaInstall: () => ({ deferred: null }),
}));

function mountCleanup() {
  return mount(SettingsSectionView, {
    global: {
      stubs: { DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true, RouterLink: true },
    },
  });
}

describe("Réglages — section Nettoyage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/quality-profiles") {
        return { profiles: [] };
      }
      if (url === "/api/v1/trash") {
        return { items: [], trash_bytes: 0, reclaimed_bytes: 0 };
      }
      return {};
    });
  });

  it("affiche la case cochée quand auto_delete_enabled est absent (défaut actif)", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "auto_delete_days", value: 14 },
        { key: "incomplete_cleanup_enabled", value: false },
      ],
    });
    const wrapper = mountCleanup();
    await flushPromises();
    const boxes = wrapper.findAll('input[type="checkbox"]');
    const autoToggle = boxes.find((b) => b.element.parentElement?.textContent?.includes("Suppression automatique"));
    expect(autoToggle?.element).toBeInstanceOf(HTMLInputElement);
    expect((autoToggle!.element as HTMLInputElement).checked).toBe(true);
    const daysInput = wrapper.find('input[type="number"]');
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
    const wrapper = mountCleanup();
    await flushPromises();
    const daysInput = wrapper.find('input[type="number"]');
    expect((daysInput.element as HTMLInputElement).disabled).toBe(true);
    expect(wrapper.text()).toContain("Désactivée : aucun fichier ne partira");
    expect(wrapper.find(".cleanup-days-muted").exists()).toBe(true);
  });
});

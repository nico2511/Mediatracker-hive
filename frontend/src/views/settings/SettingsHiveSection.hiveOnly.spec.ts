import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../../settings", () => ({
  fetchSettings: () => fetchSettingsMock(),
  patchSettings: (...args: unknown[]) => patchSettingsMock(...args),
}));

vi.mock("../../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: { value: true }, refreshHive: vi.fn() }),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/reglages/hive" }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a :href='to'><slot /></a>", props: ["to"] },
}));

import SettingsHiveSection from "./SettingsHiveSection.vue";

beforeEach(() => {
  apiMock.mockReset();
  patchSettingsMock.mockReset();
  fetchSettingsMock.mockResolvedValue({
    settings: [
      { key: "hive_enabled", value: true },
      { key: "hive_public_address", value: "" },
      { key: "hive_only", value: false },
      { key: "hive_transfer_window_start", value: 0 },
      { key: "hive_transfer_window_end", value: 24 * 60 },
    ],
  });
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/hive/identity") {
      return { enabled: true, name: "Node", instance_id: "x", pull_only: false };
    }
    if (url === "/api/v1/hive/ruches") {
      return [];
    }
    return {};
  });
});

describe("SettingsHiveSection — Ruche seule", () => {
  it("affiche l’interrupteur et l’enregistre", async () => {
    const w = mount(SettingsHiveSection);
    await flushPromises();
    const toggle = w.get('[data-testid="hive-only-toggle"]');
    expect((toggle.element as HTMLInputElement).checked).toBe(false);
    await toggle.setValue(true);
    await w.get("button.btn.primary").trigger("click");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        values: expect.objectContaining({ hive_only: true }),
      }),
    );
  });
});

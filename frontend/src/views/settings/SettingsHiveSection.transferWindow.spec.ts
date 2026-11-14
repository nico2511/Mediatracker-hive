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
      { key: "hive_transfer_window_start", value: 22 * 60 },
      { key: "hive_transfer_window_end", value: 6 * 60 },
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

describe("SettingsHiveSection — fenêtre horaire", () => {
  it("affiche les champs heure et enregistre en minutes", async () => {
    const w = mount(SettingsHiveSection);
    await flushPromises();
    const start = w.get('[data-testid="hive-transfer-start"]').element as HTMLInputElement;
    const end = w.get('[data-testid="hive-transfer-end"]').element as HTMLInputElement;
    expect(start.value).toBe("22:00");
    expect(end.value).toBe("06:00");
    await w.get('button.btn.primary').trigger("click");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        values: expect.objectContaining({
          hive_transfer_window_start: 22 * 60,
          hive_transfer_window_end: 6 * 60,
        }),
      }),
    );
  });

  it("toute la journée force 0–1440", async () => {
    const w = mount(SettingsHiveSection);
    await flushPromises();
    await w.get('[data-testid="hive-transfer-all-day"]').setValue(true);
    await w.get('button.btn.primary').trigger("click");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalledWith(
      expect.objectContaining({
        values: expect.objectContaining({
          hive_transfer_window_start: 0,
          hive_transfer_window_end: 24 * 60,
        }),
      }),
    );
  });
});
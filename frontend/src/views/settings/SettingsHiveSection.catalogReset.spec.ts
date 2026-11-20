import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

vi.mock("../../settings", () => ({
  fetchSettings: () => fetchSettingsMock(),
  patchSettings: vi.fn(),
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

const RUCHE = "cd".repeat(32);

function hiveSettingsFixture(isCreator: boolean) {
  fetchSettingsMock.mockResolvedValue({
    settings: [
      { key: "hive_enabled", value: true },
      { key: "hive_public_address", value: "https://mtr.exemple.fr" },
    ],
  });
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/hive/identity") {
      return { enabled: true, name: "Creator", instance_id: "cr-1", pull_only: false };
    }
    if (url === "/api/v1/hive/ruches") {
      return [{ ruche_id_hex: RUCHE, member_count: 1, is_creator: isCreator, pending_join_count: 0 }];
    }
    return {};
  });
}

beforeEach(() => {
  apiMock.mockReset();
  fetchSettingsMock.mockReset();
});

describe("SettingsHiveSection — reset catalogue", () => {
  it("affiche le bouton pour le créateur de ruche", async () => {
    hiveSettingsFixture(true);
    const w = mount(SettingsHiveSection);
    await flushPromises();
    expect(w.find("[data-testid=hive-catalog-reset-open]").exists()).toBe(true);
  });

  it("n’affiche pas le bouton pour un non-créateur", async () => {
    hiveSettingsFixture(false);
    const w = mount(SettingsHiveSection);
    await flushPromises();
    expect(w.find("[data-testid=hive-catalog-reset-open]").exists()).toBe(false);
  });
});

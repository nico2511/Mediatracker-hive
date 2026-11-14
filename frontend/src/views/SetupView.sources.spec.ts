import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});
vi.mock("../settings", () => ({
  fetchSettings: fetchSettingsMock,
  patchSettings: patchSettingsMock,
}));
vi.mock("../session", () => ({
  clearSession: vi.fn(),
  noteSetupCompleted: vi.fn(),
}));
vi.mock("vue-router", () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

import SetupView from "./SetupView.vue";

const storageLayout = {
  media_dir: "/data/media",
  movies_dir: "/data/media/Films",
  series_dir: "/data/media/Series",
  downloads_dir: "/data/downloads",
  trash_dir: "/data/.trash",
  warning: null,
};

describe("SetupView — étape Sources", () => {
  it("affiche les champs et Passer mène à Home Assistant", async () => {
    fetchSettingsMock.mockResolvedValue({ settings: [] });
    patchSettingsMock.mockResolvedValue(undefined);
    apiMock.mockImplementation(async (path: string, opts?: { method?: string; body?: string }) => {
      if (path === "/api/v1/setup/status") {
        return { needs_password: false, completed: false };
      }
      if (path === "/api/v1/auth/me") {
        return { id: 1 };
      }
      if (path === "/api/v1/storage") {
        return storageLayout;
      }
      if (path === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (path === "/api/v1/setup/sources") {
        return { saved: [] };
      }
      if (path === "/api/v1/integrations/jellyfin/test") {
        return { ok: true, message: "OK", user_id: null, path_warnings: [], suggested_path_map: "" };
      }
      if (path === "/api/v1/events/notifications") {
        return { events: [] };
      }
      return {};
    });

    const w = mount(SetupView, { attachTo: document.body });
    await flushPromises();

    const submitForms = async (times: number) => {
      for (let i = 0; i < times; i++) {
        const form = w.find("form");
        await form.trigger("submit.prevent");
        await flushPromises();
      }
    };

    await submitForms(3);

    const jellyfinForm = w.findAll("form").at(-1);
    expect(jellyfinForm?.text()).toMatch(/Jellyfin|URL/);
    const inputs = jellyfinForm!.findAll("input");
    const urlField = inputs[0];
    await urlField.setValue("http://jellyfin:8096");
    await jellyfinForm!.trigger("submit.prevent");
    await flushPromises();

    expect(w.text()).toContain("Sources");
    const sourcesForm = w.get("[data-testid='setup-sources-form']");
    expect(sourcesForm.text()).toContain("Wawacity");
    expect(sourcesForm.text()).toContain("Torrentio");

    const passButton = sourcesForm.findAll("button").find((b) => b.text() === "Passer");
    expect(passButton).toBeTruthy();
    await passButton!.trigger("click");
    await flushPromises();
    expect(w.text()).toContain("Home Assistant");
    w.unmount();
  });
});

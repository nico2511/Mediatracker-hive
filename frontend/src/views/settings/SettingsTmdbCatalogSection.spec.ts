import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import SettingsTmdbCatalogSection from "./SettingsTmdbCatalogSection.vue";

const patchSettingsMock = vi.hoisted(() => vi.fn(async () => undefined));

vi.mock("../../settings", () => ({
  fetchSettings: vi.fn(async () => ({
    settings: [
      { key: "tmdb_display_language", value: "fr-FR" },
      { key: "tmdb_region", value: "FR" },
      { key: "tmdb_original_languages", value: "" },
    ],
  })),
  patchSettings: patchSettingsMock,
}));

vi.mock("../../api", () => ({
  api: vi.fn(async () => ({
    items: [{ title: "Film exemple", poster_url: "/poster.jpg" }],
  })),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

describe("SettingsTmdbCatalogSection", () => {
  it("affiche les sélecteurs et l’aperçu", async () => {
    vi.useFakeTimers();
    const wrapper = mount(SettingsTmdbCatalogSection);
    await flushPromises();
    vi.advanceTimersByTime(400);
    await flushPromises();
    expect(wrapper.find('[data-testid="tmdb-display-language"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tmdb-region"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tmdb-original-languages"]').exists()).toBe(true);
    expect(wrapper.find('[data-testid="tmdb-preview-row"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("Film exemple");
    vi.useRealTimers();
  });

  it("enregistre les réglages au format attendu par patchSettings", async () => {
    const wrapper = mount(SettingsTmdbCatalogSection);
    await flushPromises();
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalledWith({
      values: {
        tmdb_display_language: "fr-FR",
        tmdb_region: "FR",
        tmdb_original_languages: "",
      },
    });
    expect(wrapper.text()).toContain("Réglages enregistrés.");
  });
});

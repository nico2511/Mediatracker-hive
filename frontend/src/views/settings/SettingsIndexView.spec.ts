import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SETTINGS_GROUPS } from "./sections";
import SettingsIndexView from "./SettingsIndexView.vue";

const serviceMocks = vi.hoisted(() => ({
  serviceStatusLoaded: null as { value: boolean } | null,
  adConnected: null as { value: boolean } | null,
  jellyfinConfigured: null as { value: boolean } | null,
  downloadReady: null as { value: boolean } | null,
  hiveEnabled: null as { value: boolean } | null,
}));

vi.mock("../../composables/useServiceStatus", async () => {
  const vue = await import("vue");
  serviceMocks.serviceStatusLoaded = vue.ref(false);
  serviceMocks.adConnected = vue.ref(true);
  serviceMocks.jellyfinConfigured = vue.ref(true);
  serviceMocks.downloadReady = vue.ref(true);
  serviceMocks.hiveEnabled = vue.ref(true);
  return {
    useServiceStatus: () => ({
      adConnected: serviceMocks.adConnected!,
      downloadReady: serviceMocks.downloadReady!,
      sourceAvailable: vue.ref({}),
      jellyfinConfigured: serviceMocks.jellyfinConfigured!,
      hiveEnabled: serviceMocks.hiveEnabled!,
      watchtowerConfigured: vue.ref(true),
      tmdbSet: vue.ref(true),
      serviceStatusLoaded: serviceMocks.serviceStatusLoaded!,
      refresh: vi.fn(),
      isSourceAvailable: () => true,
    }),
  };
});

vi.mock("vue-router", () => ({
  RouterLink: {
    props: ["to"],
    template: "<a :data-to=\"to\"><slot /></a>",
  },
}));

function groupTitles(wrapper: ReturnType<typeof mount>) {
  return wrapper.findAll(".settings-index-group-title").map((node) => node.text());
}

function badgeText(wrapper: ReturnType<typeof mount>, slug: string) {
  const el = wrapper.find(`[data-testid=settings-index-badge-${slug}]`);
  return el.exists() ? el.text() : null;
}

describe("SettingsIndexView — sommaire", () => {
  beforeEach(() => {
    serviceMocks.serviceStatusLoaded!.value = false;
    serviceMocks.adConnected!.value = true;
    serviceMocks.jellyfinConfigured!.value = true;
    serviceMocks.downloadReady!.value = true;
    serviceMocks.hiveEnabled!.value = true;
  });

  it("affiche les intertitres de groupes dans l’ordre attendu", () => {
    const w = mount(SettingsIndexView);
    expect(groupTitles(w)).toEqual(SETTINGS_GROUPS.map((g) => g.title));
  });

  it("n’affiche aucune pastille tant que le statut n’est pas chargé", () => {
    const w = mount(SettingsIndexView);
    expect(w.find("[data-testid^=settings-index-badge-]").exists()).toBe(false);
  });

  it("affiche les pastilles selon l’état des services une fois chargé", async () => {
    serviceMocks.serviceStatusLoaded!.value = true;
    serviceMocks.adConnected!.value = false;
    serviceMocks.jellyfinConfigured!.value = false;
    serviceMocks.downloadReady!.value = false;
    serviceMocks.hiveEnabled!.value = false;

    const w = mount(SettingsIndexView);
    await flushPromises();

    expect(badgeText(w, "alldebrid")).toBe("à connecter");
    expect(badgeText(w, "jellyfin")).toBe("à connecter");
    expect(badgeText(w, "sources")).toBe("à connecter");
    expect(badgeText(w, "hive")).toBe("désactivé");
    expect(badgeText(w, "compte")).toBeNull();
  });

  it("conserve toutes les entrées de section", () => {
    const w = mount(SettingsIndexView);
    const links = w.findAll("[data-testid^=settings-index-link-]");
    expect(links).toHaveLength(14);
  });
});
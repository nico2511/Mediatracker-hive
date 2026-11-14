import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";

vi.mock("../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: ref(true), refreshHive: async () => undefined }),
}));
vi.mock("../pwa/connectivity", () => ({
  useConnectivity: () => ({ queueCount: ref(0) }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({ path: "/medias" }),
  useRouter: () => ({ push: vi.fn() }),
}));

import MobileBottomNav from "./MobileBottomNav.vue";

describe("MobileBottomNav", () => {
  it("affiche l’onglet Hive quand Hive est activé", () => {
    const w = mount(MobileBottomNav, {
      global: { stubs: { SidebarIcon: true } },
    });
    const labels = w.findAll(".mobile-tab-label").map((n) => n.text());
    expect(labels).toEqual(["Acc.", "Suivre", "Médias", "Tél.", "Hive", "Plus"]);
    expect(w.find(".mobile-tabbar--six").exists()).toBe(true);
  });
});

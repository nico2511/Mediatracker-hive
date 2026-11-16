import { mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";

const routePath = ref("/");

vi.mock("vue-router", () => ({
  useRoute: () => ({ path: routePath.value, meta: routePath.value === "/decouvrir" ? { immersive: true } : {} }),
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: ref(false), refreshHive: async () => undefined }),
}));

vi.mock("../layoutCache", () => ({
  fetchHealthSummary: async () => ({ version: "", sha: "" }),
  fetchJellyfinWeb: async () => ({ url: null }),
}));

vi.mock("../session", () => ({
  sessionGate: async () => ({ signedIn: true, user: { username: "demo" }, setupCompleted: true }),
  clearSessionAndOfflineData: () => undefined,
}));

vi.mock("../api", () => ({
  api: vi.fn(),
}));

vi.mock("../components/ConnectivityBar.vue", () => ({ default: { template: "<div />" } }));
vi.mock("../components/SidebarIcon.vue", () => ({ default: { template: "<div />" } }));

import MobileBottomNav from "../components/MobileBottomNav.vue";
import AppLayout from "./AppLayout.vue";

describe("AppLayout discover", () => {
  it("masque la barre du bas en mode immersif", () => {
    routePath.value = "/decouvrir";
    const layout = mount(AppLayout, {
      global: {
        stubs: { RouterLink: true, RouterView: true, MobileBottomNav },
      },
    });
    expect(layout.findComponent(MobileBottomNav).exists()).toBe(false);
  });

  it("garde la barre du bas sur les autres routes", () => {
    routePath.value = "/medias";
    const layout = mount(AppLayout, {
      global: {
        stubs: { RouterLink: true, RouterView: true, MobileBottomNav },
      },
    });
    expect(layout.findComponent(MobileBottomNav).exists()).toBe(true);
  });
});
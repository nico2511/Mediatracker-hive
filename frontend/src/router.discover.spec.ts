import { describe, expect, it, vi } from "vitest";

import { discoverBlockedOnDesktop } from "./discoverRoute";
import router from "./router";
import DiscoverView from "./views/DiscoverView.vue";

describe("router discover", () => {
  it("expose /decouvrir", () => {
    const resolved = router.resolve("/decouvrir");
    expect(resolved.matched.at(-1)?.components?.default).toBe(DiscoverView);
    expect(resolved.meta.immersive).toBe(true);
    expect(resolved.meta.mobileOnly).toBe(true);
  });

  it("redirige vers / au-delà de 720 px", () => {
    vi.stubGlobal(
      "matchMedia",
      vi.fn().mockImplementation((query: string) => ({
        matches: query.includes("721px"),
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      })),
    );
    expect(discoverBlockedOnDesktop()).toBe(true);
    vi.unstubAllGlobals();
  });
});
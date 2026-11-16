import { describe, expect, it } from "vitest";

import { resolveDiscoverGesture } from "./discoverGestures";

describe("resolveDiscoverGesture", () => {
  it("résout les quatre gestes", () => {
    expect(resolveDiscoverGesture(120, 0)).toBe("follow");
    expect(resolveDiscoverGesture(-120, 0)).toBe("pass");
    expect(resolveDiscoverGesture(0, -120)).toBe("sheet");
    expect(resolveDiscoverGesture(0, 120)).toBe("later");
  });

  it("ignore les mouvements faibles", () => {
    expect(resolveDiscoverGesture(10, 10)).toBeNull();
  });
});
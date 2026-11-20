import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import ProfileReplacementBanner from "./ProfileReplacementBanner.vue";

vi.mock("../api", () => ({
  api: vi.fn().mockResolvedValue({ ok: true, message: "ok" }),
  ApiError: class ApiError extends Error {},
  userFacingApiError: (exc: Error) => exc.message,
}));

const proposal = {
  id: 1,
  principal_profile_id: 10,
  principal_profile_name: "Principal démo",
  repli_profile_id: 11,
  repli_profile_name: "Repli démo",
};

describe("ProfileReplacementBanner", () => {
  it("affiche le bandeau et les libellés attendus", () => {
    const wrapper = mount(ProfileReplacementBanner, {
      props: { itemId: 42, proposal },
    });
    const text = wrapper.text();
    expect(text).toContain("Version du profil principal disponible");
    expect(text).toContain("Repli démo");
    expect(text).toContain("Principal démo");
    expect(text).toContain("corbeille après remplacement");
    const buttons = wrapper.findAll("button");
    expect(buttons).toHaveLength(2);
    expect(buttons[0]?.text()).toBe("Remplacer");
    expect(buttons[1]?.text()).toBe("Ignorer");
  });
});
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import HiveBulkShareMenuItems from "./HiveBulkShareMenuItems.vue";

const apiMock = vi.fn();

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_err: unknown, detail: string) => detail,
}));

describe("HiveBulkShareMenuItems", () => {
  it("affiche une erreur et le bouton empreintes si le scope échoue", async () => {
    apiMock.mockRejectedValue(new Error("network"));
    const wrapper = mount(HiveBulkShareMenuItems, {
      props: { mediaId: 42, hiveEnabled: true },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Impossible de charger");
    expect(wrapper.text()).toContain("Réessayer");
    expect(wrapper.text()).toContain("Détails des empreintes");
  });

  it("affiche Ne plus partager quand any_shared", async () => {
    apiMock.mockResolvedValue({
      media_id: 42,
      file_count: 2,
      any_shared: true,
      can_act: true,
      pull_only: false,
      shared_count: 1,
      message: null,
    });
    const wrapper = mount(HiveBulkShareMenuItems, {
      props: { mediaId: 42, hiveEnabled: true },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Ne plus partager");
    expect(wrapper.text()).not.toContain("Partager");
  });
});

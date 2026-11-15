import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());

vi.mock("../../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("vue-router", () => ({
  RouterLink: { template: "<a @click.prevent><slot /></a>" },
  useRouter: () => ({ push: pushMock }),
}));

import HiveMediaRequestCard from "./HiveMediaRequestCard.vue";

beforeEach(() => {
  apiMock.mockReset();
  pushMock.mockReset();
});

describe("HiveMediaRequestCard", () => {
  it("affiche l'état Demandé à la ruche", () => {
    const w = mount(HiveMediaRequestCard, {
      props: { mediaItemId: 3, announced: true, promptAvailable: false },
    });
    expect(w.get("[data-testid=hive-media-request-card]").text()).toContain("Demandé à la ruche");
    expect(w.get("[data-testid=hive-request-open-mine]").exists()).toBe(true);
  });

  it("affiche Demander à la ruche pour un retrait mémorisé", () => {
    const w = mount(HiveMediaRequestCard, {
      props: { mediaItemId: 5, announced: false, promptAvailable: false, retracted: true },
    });
    expect(w.get("[data-testid=hive-request-announce]").exists()).toBe(true);
    expect(w.text()).toContain("retiré");
  });

  it("appelle announce quand le prompt est disponible", async () => {
    apiMock.mockResolvedValue(undefined);
    const w = mount(HiveMediaRequestCard, {
      props: { mediaItemId: 4, announced: false, promptAvailable: true },
    });
    await w.get("[data-testid=hive-request-announce]").trigger("click");
    await flushPromises();
    expect(apiMock).toHaveBeenCalledWith(
      "/api/v1/hive/requests/announce",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
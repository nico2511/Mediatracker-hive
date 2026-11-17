import { defineComponent, h, ref } from "vue";
import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

import { provideHiveSeriesCatalogue } from "./useHiveSeriesCatalogue";

describe("useHiveSeriesCatalogue", () => {
  it("un seul GET catalogue listé pour le parent", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.includes("/api/v1/hive/catalogue?")) {
        return { items: [{ work_key: "shell", type: "series", season: null, episode: null, versions: [] }] };
      }
      if (url.includes("/api/v1/hive/catalogue/shell")) {
        return {
          work_key: "shell",
          episodes: [
            {
              work_key: "ep-wk",
              type: "series",
              season: 1,
              episode: 1,
              versions: [{ version_key: "HD+VFF", disponible: true, source_count: 1 }],
            },
          ],
        };
      }
      throw new Error(`non simulé ${url}`);
    });

    const Parent = defineComponent({
      setup() {
        provideHiveSeriesCatalogue(ref(7010), ref(true));
        return () => h("div", [h("p"), h("p")]);
      },
    });

    apiMock.mockClear();
    mount(Parent);
    await flushPromises();

    const listCalls = apiMock.mock.calls.filter(
      (args) => typeof args[0] === "string" && args[0].startsWith("/api/v1/hive/catalogue?"),
    );
    expect(listCalls.length).toBe(1);
  });

  it("expose l’erreur catalogue au lieu de l’avaler", async () => {
    apiMock.mockRejectedValueOnce(new Error("fail"));
    let ctx: ReturnType<typeof provideHiveSeriesCatalogue> | null = null;
    const Parent = defineComponent({
      setup() {
        ctx = provideHiveSeriesCatalogue(ref(7010), ref(true));
        return () => h("div");
      },
    });
    mount(Parent);
    await flushPromises();
    expect(ctx!.error.value).toBe("Catalogue Hive indisponible.");
  });
});
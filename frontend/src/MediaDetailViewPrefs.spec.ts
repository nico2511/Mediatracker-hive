import { flushPromises, mount } from "@vue/test-utils";
import { ref } from "vue";
import { describe, expect, it, vi } from "vitest";

import filmFixture from "./revue/v1_film_non_suivi.json";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());

vi.mock("./api", () => ({
  api: apiMock,
  ApiError: class ApiError extends Error {},
  userFacingApiError: (_e: unknown, f: string) => f,
}));
vi.mock("./settings", () => ({ fetchSettings: fetchSettingsMock }));
vi.mock("./composables/useProgressStream", () => ({ useProgressStream: () => undefined }));
vi.mock("./composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: ref(false) }),
}));
vi.mock("vue-router", () => ({
  useRoute: () => ({
    params: { id: String(filmFixture.media.id) },
    query: { reglages: "ouverts" },
  }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import MediaDetailView from "./views/MediaDetailView.vue";

const profiles = [
  {
    id: 1,
    name: "Compact",
    languages: ["VF"],
    min_resolution: "HD",
    max_resolution: "HD",
    min_size_mb: null,
    max_size_mb: 2048,
    exclude_keywords: [],
    is_default: true,
  },
];

function stubApi() {
  const media = {
    ...filmFixture.media,
    allow_fallback: true,
    fallback_profile_ids: null,
    quality_profile_id: 1,
  };
  apiMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url === `/api/v1/media/${media.id}` && init?.method === "PATCH") {
      return media;
    }
    if (url === `/api/v1/media/${media.id}`) {
      return media;
    }
    if (url === `/api/v1/media/${media.id}/items`) {
      return filmFixture.items;
    }
    if (url === "/api/v1/quality-profiles") {
      return { profiles };
    }
    if (url.startsWith(`/api/v1/media/${media.id}/identity`)) {
      return [];
    }
    if (url.startsWith(`/api/v1/media/${media.id}/library-folders`)) {
      return { items: [] };
    }
    if (url.startsWith(`/api/v1/media/${media.id}/jellyfin`)) {
      throw new Error("off");
    }
    throw new Error(`non simulé ${url}`);
  });
}

describe("Fiche — savePrefs", () => {
  it("n’envoie pas pref_quality ni pref_language", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "profile_fallback_order", value: "1" }],
    });
    stubApi();
    const wrapper = mount(MediaDetailView, {
      global: {
        stubs: { Teleport: true, RouterLink: true, TmdbPosterImg: true, StatusIcon: true, SourceRank: true },
        attachTo: document.body,
      },
    });
    await flushPromises();
    const form = wrapper.find("form.fiche-profile-prefs");
    expect(form.exists()).toBe(true);
    const submit = form.find("button.primary");
    expect(submit.attributes("disabled")).toBeUndefined();
    await form.trigger("submit.prevent");
    await flushPromises();
    const patchCall = apiMock.mock.calls.find(
      (c) => c[0] === `/api/v1/media/${filmFixture.media.id}` && c[1]?.method === "PATCH",
    );
    expect(patchCall).toBeTruthy();
    const body = JSON.parse(patchCall![1]!.body as string) as Record<string, unknown>;
    expect(body).not.toHaveProperty("pref_quality");
    expect(body).not.toHaveProperty("pref_language");
    expect(body.fallback_profile_ids).toBeNull();
  });

  it("laisse source_order à null si l’ordre de la fiche n’est pas surchargé", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "profile_fallback_order", value: "1" },
        { key: "download_source_order", value: "comet,wawacity" },
      ],
    });
    stubApi();
    const wrapper = mount(MediaDetailView, {
      global: {
        stubs: { Teleport: true, RouterLink: true, TmdbPosterImg: true, StatusIcon: true, SourceRank: true },
        attachTo: document.body,
      },
    });
    await flushPromises();
    const form = wrapper.find("form.fiche-profile-prefs");
    await form.trigger("submit.prevent");
    await flushPromises();
    const patchCall = apiMock.mock.calls.find(
      (c) => c[0] === `/api/v1/media/${filmFixture.media.id}` && c[1]?.method === "PATCH",
    );
    expect(patchCall).toBeTruthy();
    const body = JSON.parse(patchCall![1]!.body as string) as Record<string, unknown>;
    expect(body.source_order).toBeNull();
  });
});

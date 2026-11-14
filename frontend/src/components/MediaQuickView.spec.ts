import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi, beforeEach } from "vitest";

import MediaQuickView from "./MediaQuickView.vue";

const apiMock = vi.hoisted(() => vi.fn());
const routerPush = vi.hoisted(() => vi.fn());

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: routerPush }),
}));

vi.mock("../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api")>();
  return { ...actual, api: apiMock };
});

const mountFollow = (follow = true) =>
  mount(MediaQuickView, {
    props: {
      target: {
        title: "Fiche-parcours",
        mediaType: "film",
        mediaId: 42,
        followed: false,
      },
      follow,
    },
    global: {
      stubs: {
        Teleport: true,
        RouterLink: true,
        TmdbPosterImg: true,
        StatusIcon: true,
      },
    },
  });

function followBody(call: unknown[]): Record<string, unknown> | undefined {
  const init = call[1] as RequestInit | undefined;
  if (!init?.body || typeof init.body !== "string") {
    return undefined;
  }
  return JSON.parse(init.body) as Record<string, unknown>;
}

describe("MediaQuickView confirm()", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation(async (url: string) => {
      if (url.endsWith("/follow")) {
        return { id: 42 };
      }
      return { id: 42 };
    });
  });

  it("Oui : ferme le dialogue sans /check, start_now=true", async () => {
    const wrapper = mountFollow();
    await flushPromises();
    await wrapper.findAll("button").find((btn) => btn.text() === "Oui")!.trigger("click");
    await flushPromises();
    const checkCall = apiMock.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].endsWith("/check"),
    );
    expect(checkCall).toBeFalsy();
    const followCall = apiMock.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].endsWith("/follow"),
    );
    expect(followCall).toBeTruthy();
    expect(followBody(followCall!)).toMatchObject({ start_now: true });
    expect(wrapper.text()).toContain("Suivi enregistré, recherche lancée");
    expect(wrapper.text()).not.toContain("Commencer à télécharger");
    expect(wrapper.emitted("changed")).toBeTruthy();
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("Non : follow sans appel /check, start_now=false", async () => {
    const wrapper = mountFollow();
    await flushPromises();
    await wrapper.findAll("button").find((btn) => btn.text() === "Non")!.trigger("click");
    await flushPromises();
    const checkCall = apiMock.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].endsWith("/check"),
    );
    expect(checkCall).toBeFalsy();
    const followCall = apiMock.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].endsWith("/follow"),
    );
    expect(followCall).toBeTruthy();
    expect(followBody(followCall!)).toMatchObject({ start_now: false });
    expect(wrapper.text()).toContain("Suivi enregistré");
    expect(wrapper.text()).not.toContain("Commencer à télécharger");
    expect(wrapper.emitted("close")).toBeFalsy();
  });

  it("Annuler : pas d'appel follow ni check", async () => {
    const wrapper = mountFollow();
    await flushPromises();
    await wrapper.findAll("button").find((btn) => btn.text() === "Annuler")!.trigger("click");
    await flushPromises();
    const wrote = apiMock.mock.calls.filter(
      (call) =>
        typeof call[0] === "string" &&
        (call[0].endsWith("/follow") || call[0].endsWith("/check")),
    );
    expect(wrote).toHaveLength(0);
  });

  it("calls POST /media/{id}/follow for an existing fiche", async () => {
    const wrapper = mountFollow();
    await flushPromises();
    const noButton = wrapper.findAll("button").find((btn) => btn.text() === "Non");
    expect(noButton).toBeTruthy();
    await noButton!.trigger("click");
    await flushPromises();
    const followCall = apiMock.mock.calls.find(
      (call) => typeof call[0] === "string" && call[0].endsWith("/follow"),
    );
    expect(followCall).toBeTruthy();
    expect(followCall![0]).toBe("/api/v1/media/42/follow");
    const init = followCall![1] as RequestInit | undefined;
    expect(init?.method).toBe("POST");
  });
});

describe("MediaQuickView openFiche()", () => {
  beforeEach(() => {
    apiMock.mockReset();
    routerPush.mockReset();
    apiMock.mockResolvedValue({
      id: 42,
      type: "film",
      title: "Fiche Démo",
      followed: true,
    });
  });

  it("clic sur Fiche : close émis et navigation vers /medias/<id>", async () => {
    const wrapper = mount(MediaQuickView, {
      props: {
        target: {
          title: "Fiche Démo",
          mediaType: "film",
          mediaId: 42,
          followed: true,
        },
      },
      global: {
        stubs: {
          Teleport: true,
          RouterLink: true,
          TmdbPosterImg: true,
          StatusIcon: true,
        },
      },
    });
    await flushPromises();
    const fiche = wrapper.findAll("button").find((btn) => btn.text() === "Fiche");
    expect(fiche).toBeTruthy();
    await fiche!.trigger("click");
    expect(wrapper.emitted("close")).toHaveLength(1);
    expect(routerPush).toHaveBeenCalledWith("/medias/42");
  });
});

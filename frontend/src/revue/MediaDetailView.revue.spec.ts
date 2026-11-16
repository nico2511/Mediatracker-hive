import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import filmNonSuivi from "./v1_film_non_suivi.json";
import filmCorbeille from "./v2_film_en_corbeille.json";
import serieNonSuivie from "./v3_serie_non_suivie.json";

const apiMock = vi.hoisted(() => vi.fn());
const pushMock = vi.hoisted(() => vi.fn());
let current: { media: Record<string, unknown>; items: { seasons: unknown[] } } = filmNonSuivi as never;

vi.mock("../api", () => ({ api: apiMock, userFacingApiError: (_e: unknown, f: string) => f }));
vi.mock("../settings", () => ({ fetchSettings: async () => ({ settings: [] }) }));
vi.mock("../composables/useProgressStream", () => ({ useProgressStream: () => undefined }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: String(current.media.id) }, query: {} }),
  useRouter: () => ({ push: pushMock, replace: pushMock }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import MediaDetailView from "../views/MediaDetailView.vue";

function setup(data: unknown, patch: Record<string, unknown> = {}) {
  const d = JSON.parse(JSON.stringify(data)) as typeof current;
  Object.assign(d.media, patch);
  current = d;
  apiMock.mockReset();
  pushMock.mockReset();
  apiMock.mockImplementation(async (url: string, init?: RequestInit) => {
    const id = current.media.id;
    if (url === `/api/v1/media/${id}`) {
      return current.media;
    }
    if (url === `/api/v1/media/${id}/items`) {
      return current.items;
    }
    if (url.startsWith("/api/v1/quality-profiles")) {
      return { profiles: [] };
    }
    if (url.startsWith(`/api/v1/media/${id}?trash=`)) {
      return undefined;
    }
    if (url === "/api/v1/cleanup/files") {
      return { bytes: 0 };
    }
    if (url === `/api/v1/media/${id}/unfollow`) {
      return {};
    }
    if (url.startsWith(`/api/v1/media/${id}/identity`)) {
      return [];
    }
    if (url.startsWith(`/api/v1/media/${id}/library-folders`)) {
      return { items: [] };
    }
    if (url.startsWith(`/api/v1/media/${id}/jellyfin`)) {
      throw new Error("jellyfin off");
    }
    if (typeof url === "string" && url === `/api/v1/media/${id}` && init?.method === "PATCH") {
      return current.media;
    }
    throw new Error(`non simulé ${url}`);
  });
}

async function mountView() {
  const w = mount(MediaDetailView, {
    global: { stubs: { Teleport: true, RouterLink: true, TmdbPosterImg: true, StatusIcon: true } },
    attachTo: document.body,
  });
  await flushPromises();
  return w;
}

function section(w: ReturnType<typeof mount>) {
  return w.find("details.fiche-suivi");
}

function btn(w: ReturnType<typeof mount>, root: string, text: string) {
  return w.find(root).findAll("button").find((b) => b.text().trim() === text);
}

describe("PR166 — section Suppression et fin du suivi", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fiche non suivie : Suivre à nouveau, Plus d'actions, zone danger absente", async () => {
    setup(serieNonSuivie);
    const w = await mountView();
    expect(section(w).exists()).toBe(false);
    expect(w.find("[data-testid='fiche-follow-again']").exists()).toBe(true);
    expect(w.find("[data-testid='fiche-unfollowed-more']").exists()).toBe(true);
  });

  it("Suivre à nouveau envoie PATCH followed=true", async () => {
    setup(serieNonSuivie);
    const w = await mountView();
    const b = w.find("[data-testid='fiche-follow-again']");
    expect(b.exists()).toBe(true);
    await b.trigger("click");
    await flushPromises();
    const call = apiMock.mock.calls.find((c) => c[1]?.method === "PATCH");
    expect(JSON.parse(call![1]!.body as string).followed).toBe(true);
  });

  it("Supprimer tous les fichiers demande toujours confirmation", async () => {
    setup(serieNonSuivie);
    const conf = vi.spyOn(window, "confirm").mockReturnValue(false);
    const w = await mountView();
    await w.find("[data-testid='fiche-unfollowed-more'] .action-menu-trigger").trigger("click");
    await flushPromises();
    await w.findAll("button").find((b) => b.text().includes("Supprimer tous les fichiers"))!.trigger("click");
    await flushPromises();
    expect(conf).toHaveBeenCalledTimes(1);
    expect(apiMock.mock.calls.find((c) => c[0] === "/api/v1/cleanup/files")).toBeUndefined();
    conf.mockReturnValue(true);
    await w.find("[data-testid='fiche-unfollowed-more'] .action-menu-trigger").trigger("click");
    await flushPromises();
    await w.findAll("button").find((b) => b.text().includes("Supprimer tous les fichiers"))!.trigger("click");
    await flushPromises();
    expect(apiMock.mock.calls.find((c) => c[0] === "/api/v1/cleanup/files")).toBeTruthy();
  });

  it("Supprimer la fiche : corbeille par défaut puis DELETE ?trash=true/false", async () => {
    setup(serieNonSuivie);
    const w = await mountView();
    await w.find("[data-testid='fiche-unfollowed-more'] .action-menu-trigger").trigger("click");
    await flushPromises();
    await w.findAll("button").find((b) => b.text().trim() === "Supprimer la fiche…")!.trigger("click");
    await flushPromises();
    const radios = w.findAll("form.dialog input[type=radio]");
    expect((radios[0].element as HTMLInputElement).checked).toBe(true);
    await w.find("form.dialog").trigger("submit");
    await flushPromises();
    const del = apiMock.mock.calls.find((c) => c[1]?.method === "DELETE");
    expect(del![0]).toMatch(/\?trash=true$/);
    expect(pushMock).toHaveBeenCalledWith("/medias");

    setup(serieNonSuivie);
    const w2 = await mountView();
    await w2.find("[data-testid='fiche-unfollowed-more'] .action-menu-trigger").trigger("click");
    await flushPromises();
    await w2.findAll("button").find((b) => b.text().trim() === "Supprimer la fiche…")!.trigger("click");
    await flushPromises();
    await w2.findAll("form.dialog input[type=radio]")[1].setValue(true);
    await w2.find("form.dialog").trigger("submit");
    await flushPromises();
    const del2 = apiMock.mock.calls.find((c) => c[1]?.method === "DELETE");
    expect(del2![0]).toMatch(/\?trash=false$/);
  });

  it("fiche suivie : Ne plus suivre actif, pas de raison d'indisponibilité", async () => {
    setup(serieNonSuivie, { followed: true });
    const w = await mountView();
    const buttons = section(w).findAll("button").map((b) => [b.text().trim(), b.attributes("disabled") !== undefined]);
    expect(buttons[0]).toEqual(["Ne plus suivre", false]);
    expect(section(w).text()).not.toContain("Indisponible : la fiche");
    expect(w.find(".fiche-actions").text()).not.toContain("Suivre à nouveau");
  });

  it("film avec fichier réel (on_disk, non scanné) : Supprimer le film actif", async () => {
    setup(filmNonSuivi);
    const w = await mountView();
    expect(section(w).exists()).toBe(false);
    expect(w.find("[data-testid='fiche-unfollowed-more']").exists()).toBe(true);
    expect(w.find(".film-file-panel").exists()).toBe(true);
  });

  it("film en corbeille : Supprimer le film grisé", async () => {
    setup(filmCorbeille);
    const w = await mountView();
    expect(section(w).exists()).toBe(false);
    expect(w.find("[data-testid='fiche-unfollowed-more']").exists()).toBe(true);
  });
});

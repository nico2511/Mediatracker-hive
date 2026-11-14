import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const rows = vi.hoisted(() => ({ value: [] as unknown[] }));
const state = vi.hoisted(() => ({ configured: true, active: 0 }));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});
vi.mock("../settings", () => ({
  fetchSettings: async () => ({ settings: rows.value }),
  patchSettings: async () => ({}),
}));
vi.mock("../pwa/pwaInstall", () => ({ openInstallPanel: () => undefined, usePwaInstall: () => ({ deferred: null }) }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { section: "maj" }, query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import { ApiError } from "../api";
import SettingsSectionView from "../views/settings/SettingsSectionView.vue";

const reload = vi.fn();
let postImpl: () => Promise<unknown> = async () => ({
  ok: true,
  detail: "Demande envoyée à Watchtower.",
  code: "sent",
});
let healthSeq: Array<() => unknown> = [];
let postDeferred: { resolve: (v: unknown) => void } | null = null;

function payload() {
  return {
    current_sha: "abc123def456",
    build_date: null,
    detail: "x",
    update_available: false,
    remote_sha: null,
    checked_at: null,
    version: "2.0.0",
    watchtower_configured: state.configured,
    active_downloads: state.active,
  };
}

async function mountMaj() {
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/ha/update") return payload();
    if (url === "/api/v1/ha/actions/update-now") return postImpl();
    if (url === "/api/v1/health") {
      const next = healthSeq.length > 1 ? healthSeq.shift()! : healthSeq[0];
      return next();
    }
    if (url.startsWith("/api/v1/quality-profiles")) return { profiles: [] };
    if (url.startsWith("/api/v1/settings")) return { settings: rows.value };
    return {};
  });
  const w = mount(SettingsSectionView, {
    global: { stubs: { RouterLink: true, DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true } },
    attachTo: document.body,
  });
  await flushPromises();
  await flushPromises();
  return w;
}

const btn = (w: ReturnType<typeof mount>) => w.find(".settings-update-now-btn");
const posts = () => apiMock.mock.calls.filter((c) => c[0] === "/api/v1/ha/actions/update-now").length;

beforeEach(() => {
  state.configured = true;
  state.active = 0;
  rows.value = [
    { key: "watchtower_url", value: "http://watchtower:8080", secret: false, set: true },
    { key: "watchtower_token", value: null, secret: true, set: true },
    { key: "notify_on_update", value: false, secret: false, set: true },
  ];
  reload.mockReset();
  postDeferred = null;
  Object.defineProperty(window, "location", { configurable: true, value: { ...window.location, reload } });
  postImpl = async () => ({
    ok: true,
    detail: "Demande envoyée à Watchtower.",
    code: "sent",
  });
  healthSeq = [() => ({ version: "2.0.0", sha: "aaaaaaa" })];
});
afterEach(() => {
  vi.useRealTimers();
});

describe("Réglages maj — Mettre à jour maintenant", () => {
  it("non configuré : bouton présent, grisé ; raison ne nomme que le champ manquant", async () => {
    state.configured = false;
    rows.value = [
      { key: "watchtower_url", value: "http://watchtower:8080", secret: false, set: true },
      { key: "watchtower_token", value: null, secret: true, set: false },
      { key: "notify_on_update", value: false, secret: false, set: true },
    ];
    const w = await mountMaj();
    expect(btn(w).exists()).toBe(true);
    expect((btn(w).element as HTMLButtonElement).disabled).toBe(true);
    const line = w.find(".settings-update-unavail").text();
    expect(line).toContain("jeton");
    expect(line).toContain("Accès API");
    expect(line).not.toMatch(/\bURL\b/);
    w.unmount();
  });

  it("configuré au repos : bouton actif et encadré version", async () => {
    const w = await mountMaj();
    expect(btn(w).text()).toContain("Mettre à jour maintenant");
    expect((btn(w).element as HTMLButtonElement).disabled).toBe(false);
    expect(w.find(".settings-update-version").text()).toMatch(/2\.0\.0/);
    w.unmount();
  });

  it("en cours : libellé Demande en cours…", async () => {
    postImpl = () =>
      new Promise((resolve) => {
        postDeferred = { resolve };
      });
    const w = await mountMaj();
    void btn(w).trigger("click");
    await flushPromises();
    expect(btn(w).text()).toContain("Demande en cours");
    postDeferred?.resolve({ ok: true, detail: "Demande envoyée.", code: "sent" });
    await flushPromises();
    w.unmount();
  });

  it("502 injoignable (code backend) → Réessayer, pas « envoyée »", async () => {
    postImpl = async () => {
      throw new ApiError(502, "Watchtower est injoignable.", undefined, "unreachable");
    };
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(btn(w).text()).toContain("Réessayer");
    expect(w.find(".settings-update-error").text()).toContain("injoignable");
    w.unmount();
  });

  it("502 jeton refusé (code backend) → Réessayer", async () => {
    postImpl = async () => {
      throw new ApiError(502, "Watchtower a refusé le jeton.", undefined, "token_refused");
    };
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(btn(w).text()).toContain("Réessayer");
    expect(w.find(".settings-update-error").text()).toContain("jeton");
    w.unmount();
  });

  it("502 watchtower_error → encadré rouge et Réessayer, pas « envoyée »", async () => {
    postImpl = async () => {
      throw new ApiError(502, "Watchtower a répondu une erreur.", undefined, "watchtower_error");
    };
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(btn(w).text()).toContain("Réessayer");
    expect(w.find(".settings-update-error").text()).toContain("erreur");
    expect(btn(w).text()).not.toContain("Demande envoyée");
    w.unmount();
  });

  it("envoyée : rechargement quand le service revient après coupure", async () => {
    vi.useFakeTimers();
    healthSeq = [
      () => ({ version: "2.0.0", sha: "aaaaaaa" }),
      () => {
        throw new TypeError("Failed to fetch");
      },
      () => ({ version: "2.0.0", sha: "bbbbbbb" }),
    ];
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    for (let i = 0; i < 4; i++) {
      await vi.advanceTimersByTimeAsync(3000);
      await flushPromises();
    }
    expect(reload).toHaveBeenCalled();
    w.unmount();
  });

  it("envoyée : rechargement sans coupure si sha change", async () => {
    vi.useFakeTimers();
    healthSeq = [() => ({ version: "2.0.0", sha: "aaaaaaa" }), () => ({ version: "2.0.0", sha: "bbbbbbb" })];
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    await vi.advanceTimersByTimeAsync(3000);
    await flushPromises();
    expect(reload).toHaveBeenCalled();
    w.unmount();
  });

  it("envoyée : délai 3 min sans redémarrage", async () => {
    vi.useFakeTimers();
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(btn(w).text()).toContain("Demande envoyée");
    expect((btn(w).element as HTMLButtonElement).disabled).toBe(true);
    await vi.advanceTimersByTimeAsync(181_000);
    await flushPromises();
    expect(w.text()).toContain("Watchtower n'a pas relancé MediaTracker. Voir ses journaux.");
    w.unmount();
  });

  it("déjà à jour (code already_up_to_date)", async () => {
    postImpl = async () => ({
      ok: true,
      code: "already_up_to_date",
      detail: "Rien à installer. Watchtower n'a pas trouvé d'image plus récente : MediaTracker est déjà à jour.",
    });
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(w.find(".settings-update-muted").text()).toContain("déjà à jour");
    expect(btn(w).text()).toContain("Mettre à jour maintenant");
    w.unmount();
  });

  it("confirmation si téléchargements actifs ; refus = aucun POST", async () => {
    state.active = 2;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(posts()).toBe(0);
    confirm.mockReturnValue(true);
    await btn(w).trigger("click");
    await flushPromises();
    expect(posts()).toBe(1);
    w.unmount();
  });

  it("sans téléchargement actif : pas de boîte de confirmation", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(confirm).not.toHaveBeenCalled();
    expect(posts()).toBe(1);
    w.unmount();
  });

  it("coupure réseau pendant l'appel = envoyée", async () => {
    postImpl = async () => {
      throw new TypeError("Failed to fetch");
    };
    const w = await mountMaj();
    await btn(w).trigger("click");
    await flushPromises();
    expect(btn(w).text()).toContain("Demande envoyée");
    w.unmount();
  });
});

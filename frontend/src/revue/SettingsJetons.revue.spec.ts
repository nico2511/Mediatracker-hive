import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const rows = vi.hoisted(() => ({ value: [] as unknown[] }));
const section = vi.hoisted(() => ({ value: "jetons" }));
const patchSettings = vi.hoisted(() => vi.fn(async () => ({})));
const updatePayload = vi.hoisted(() => ({
  configured: false,
  active: 0,
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, api: apiMock };
});
vi.mock("../settings", () => ({
  fetchSettings: async () => ({ settings: rows.value }),
  patchSettings: (...args: unknown[]) => patchSettings(...args),
}));
vi.mock("../pwa/pwaInstall", () => ({ openInstallPanel: () => undefined, usePwaInstall: () => ({ deferred: null }) }));
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { section: section.value }, query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import SettingsSectionView from "../views/settings/SettingsSectionView.vue";

function haUpdateResponse() {
  return {
    current_sha: "abc123def456",
    build_date: null,
    detail: "x",
    update_available: false,
    remote_sha: null,
    checked_at: null,
    version: "2.0.0",
    watchtower_configured: updatePayload.configured,
    active_downloads: updatePayload.active,
  };
}

async function mountSection(slug: string) {
  section.value = slug;
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/ha/update") return haUpdateResponse();
    if (url.startsWith("/api/v1/quality-profiles")) return { profiles: [] };
    if (url === "/api/v1/tokens") return { items: [] };
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

beforeEach(() => {
  updatePayload.configured = false;
  updatePayload.active = 0;
  patchSettings.mockClear();
  rows.value = [
    { key: "ghcr_read_token", value: null, secret: true, set: false },
    { key: "github_update_token", value: null, secret: true, set: false },
    { key: "watchtower_url", value: "", secret: false, set: false },
    { key: "watchtower_token", value: null, secret: true, set: false },
    { key: "notify_on_update", value: false, secret: false, set: true },
  ];
});

describe("Réglages jetons — secrets distants et Accès API", () => {
  it("deux blocs distincts avec avertissement Accès API / Watchtower", async () => {
    const w = await mountSection("jetons");
    expect(w.find("[data-testid=outbound-secrets-block]").exists()).toBe(true);
    expect(w.find("[data-testid=api-access-block]").exists()).toBe(true);
    expect(w.find("[data-testid=outbound-secrets-block]").text()).toContain("Secrets pour services distants");
    expect(w.find("[data-testid=api-access-block]").text()).toMatch(/Ne sert pas pour Watchtower/i);
    w.unmount();
  });

  it("états non renseignés et URL sans placeholder ressemblant à une valeur", async () => {
    const w = await mountSection("jetons");
    expect(w.find("[data-testid=ghcr-secret-status]").text()).toBe("non renseigné");
    expect(w.find("[data-testid=watchtower-url-status]").text()).toContain("non renseignée");
    const urlInput = w.find("[data-testid=watchtower-url-input]").element as HTMLInputElement;
    expect(urlInput.placeholder).toBe("");
    expect(urlInput.value).toBe("");
    expect(w.find("[data-testid=watchtower-url-input]").element.parentElement?.textContent).toContain(
      "http://watchtower:8080",
    );
    w.unmount();
  });

  it("URL enregistrée affichée dans l'état, pas dans le placeholder", async () => {
    rows.value = [
      ...rows.value.filter((r) => (r as { key: string }).key !== "watchtower_url"),
      { key: "watchtower_url", value: "http://watchtower:8080", secret: false, set: true },
    ];
    const w = await mountSection("jetons");
    expect(w.find("[data-testid=watchtower-url-status]").text()).toContain("http://watchtower:8080");
    const urlInput = w.find("[data-testid=watchtower-url-input]").element as HTMLInputElement;
    expect(urlInput.placeholder).toBe("");
    w.unmount();
  });

  it("enregistrement : confirmation, états mis à jour, maj sans rechargement", async () => {
    rows.value = [
      { key: "ghcr_read_token", value: null, secret: true, set: false },
      { key: "github_update_token", value: null, secret: true, set: false },
      { key: "watchtower_url", value: "", secret: false, set: false },
      { key: "watchtower_token", value: null, secret: true, set: false },
      { key: "notify_on_update", value: false, secret: false, set: true },
    ];
    let w = await mountSection("jetons");
    await w.find("[data-testid=watchtower-url-input]").setValue("http://watchtower:8080");
    updatePayload.configured = true;
    await w.find("[data-testid=watchtower-token-input]").setValue("secret-token-value");
    await w.find("[data-testid=outbound-secrets-form]").trigger("submit.prevent");
    await flushPromises();
    expect(patchSettings).toHaveBeenCalled();
    expect(w.find(".settings-saved").text()).toContain("Enregistré");
    expect(w.find("[data-testid=watchtower-secret-status]").text()).toBe("enregistré");
    expect(w.find("[data-testid=watchtower-url-status]").text()).toContain("http://watchtower:8080");
    expect(apiMock.mock.calls.some((c) => c[0] === "/api/v1/ha/update")).toBe(true);
    w.unmount();

    rows.value = [
      { key: "watchtower_url", value: "http://watchtower:8080", secret: false, set: true },
      { key: "watchtower_token", value: null, secret: true, set: true },
      { key: "notify_on_update", value: false, secret: false, set: true },
    ];
    w = await mountSection("maj");
    const btn = w.find(".settings-update-now-btn");
    expect((btn.element as HTMLButtonElement).disabled).toBe(false);
    expect(w.find(".settings-update-unavail").exists()).toBe(false);
    w.unmount();
  });

  it("erreur d'enregistrement affichée", async () => {
    patchSettings.mockRejectedValueOnce(new Error("refus"));
    const w = await mountSection("jetons");
    await w.find("[data-testid=watchtower-url-input]").setValue("http://watchtower:8080");
    await w.find("[data-testid=watchtower-token-input]").setValue("x");
    await w.find("[data-testid=outbound-secrets-form]").trigger("submit.prevent");
    await flushPromises();
    expect(w.find(".error").exists()).toBe(true);
    expect(w.find(".settings-saved").exists()).toBe(false);
    w.unmount();
  });
});

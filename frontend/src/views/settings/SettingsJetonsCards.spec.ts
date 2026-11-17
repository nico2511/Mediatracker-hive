import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const rows = vi.hoisted(() => ({ value: [] as unknown[] }));
const patchSettings = vi.hoisted(() => vi.fn(async () => ({})));
const serviceStatusRefsForTests = vi.hoisted(() => ({
  adConnected: null as { value: boolean } | null,
  jellyfinConfigured: null as { value: boolean } | null,
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return { ...actual, api: apiMock };
});
vi.mock("../../settings", () => ({
  fetchSettings: async () => ({ settings: rows.value }),
  patchSettings: (...args: unknown[]) => patchSettings(...args),
}));
vi.mock("../../pwa/pwaInstall", () => ({ openInstallPanel: () => undefined, usePwaInstall: () => ({ deferred: null }) }));
vi.mock("../../composables/useServiceStatus", async () => {
  const vue = await import("vue");
  const actual = await vi.importActual<typeof import("../../composables/useServiceStatus")>(
    "../../composables/useServiceStatus",
  );
  const adConnected = vue.ref(false);
  const jellyfinConfigured = vue.ref(false);
  serviceStatusRefsForTests.adConnected = adConnected;
  serviceStatusRefsForTests.jellyfinConfigured = jellyfinConfigured;
  return {
    ...actual,
    useServiceStatus: () => ({
      adConnected,
      downloadReady: vue.ref(true),
      sourceAvailable: vue.shallowRef({}),
      jellyfinConfigured,
      hiveEnabled: vue.ref(false),
      watchtowerConfigured: vue.ref(false),
      tmdbSet: vue.ref(false),
      serviceStatusLoaded: vue.ref(true),
      refresh: vi.fn(),
      isSourceAvailable: () => true,
    }),
  };
});
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { section: "jetons" }, query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
  RouterLink: { template: "<a><slot /></a>" },
}));

import SettingsSectionView from "./SettingsSectionView.vue";

async function mountJetons() {
  apiMock.mockReset();
  apiMock.mockImplementation(async (url: string) => {
    if (url === "/api/v1/ha/update") {
      return {
        current_sha: "abc",
        build_date: null,
        detail: "x",
        update_available: false,
        remote_sha: null,
        checked_at: null,
        version: "2.0.0",
        watchtower_configured: false,
        active_downloads: 0,
      };
    }
    if (url.startsWith("/api/v1/quality-profiles")) return { profiles: [] };
    if (url === "/api/v1/tokens") return { items: [] };
    if (url === "/api/v1/integrations/alldebrid/account") {
      return {
        connected: serviceStatusRefsForTests.adConnected?.value ?? false,
        pin_phase: "idle",
        source: "none",
        env_locked: false,
      };
    }
    return {};
  });
  const wrapper = mount(SettingsSectionView, {
    global: { stubs: { RouterLink: true, DatabaseBackupRestore: true, DiskSpaceCard: true, SourceRank: true } },
    attachTo: document.body,
  });
  await flushPromises();
  await flushPromises();
  return wrapper;
}

beforeEach(() => {
  patchSettings.mockClear();
  serviceStatusRefsForTests.adConnected!.value = false;
  serviceStatusRefsForTests.jellyfinConfigured!.value = false;
  rows.value = [
    { key: "tmdb_read_token", value: null, secret: true, set: false },
    { key: "ghcr_read_token", value: null, secret: true, set: false },
    { key: "github_update_token", value: null, secret: true, set: false },
    { key: "watchtower_url", value: "", secret: false, set: false },
    { key: "watchtower_token", value: null, secret: true, set: false },
    { key: "notify_on_update", value: false, secret: false, set: true },
  ];
});

describe("Réglages jetons — cartes services", () => {
  it("affiche six cartes avec pastilles et liens de documentation", async () => {
    const wrapper = await mountJetons();
    expect(wrapper.find("[data-testid=service-card-tmdb]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=service-card-alldebrid]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=service-card-jellyfin]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=service-card-github]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=service-card-watchtower]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=api-access-block]").exists()).toBe(true);

    expect(wrapper.find("[data-testid=tmdb-card-status]").text()).toBe("Non renseigné");
    expect(wrapper.find("[data-testid=alldebrid-card-status]").text()).toBe("Non connecté");
    expect(wrapper.find("[data-testid=jellyfin-card-status]").text()).toBe("Non renseigné");
    expect(wrapper.find("[data-testid=github-card-status]").text()).toBe("Non renseigné");
    expect(wrapper.find("[data-testid=watchtower-card-status]").text()).toBe("Non renseigné");
    expect(wrapper.find("[data-testid=api-access-card-status]").text()).toBe("Non renseigné");

    const alldebridDoc = wrapper.find("[data-testid=alldebrid-doc]");
    expect(alldebridDoc.text()).toBe("Doc AllDebrid ↗");
    expect(alldebridDoc.attributes("href")).toBe("https://docs.alldebrid.com/");
    expect(alldebridDoc.attributes("target")).toBe("_blank");
    expect(alldebridDoc.attributes("rel")).toBe("noreferrer noopener");

    const jellyfinDoc = wrapper.find("[data-testid=jellyfin-doc]");
    expect(jellyfinDoc.text()).toBe("Doc Jellyfin ↗");
    expect(jellyfinDoc.attributes("href")).toBe("https://jellyfin.org/docs/");
    expect(jellyfinDoc.attributes("target")).toBe("_blank");
    expect(jellyfinDoc.attributes("rel")).toBe("noreferrer noopener");

    const jellyfinApiDoc = wrapper.find("[data-testid=jellyfin-api-doc]");
    expect(jellyfinApiDoc.text()).toBe("API Jellyfin ↗");
    expect(jellyfinApiDoc.attributes("href")).toBe("https://api.jellyfin.org/");
    expect(jellyfinApiDoc.attributes("target")).toBe("_blank");
    expect(jellyfinApiDoc.attributes("rel")).toBe("noreferrer noopener");

    const jellyfinNetworking = wrapper.find("[data-testid=jellyfin-networking-doc]");
    expect(jellyfinNetworking.text()).toBe("Réseau Jellyfin ↗");
    expect(jellyfinNetworking.attributes("href")).toBe("https://jellyfin.org/docs/general/networking/");

    const tmdbStart = wrapper.find("[data-testid=tmdb-doc-getting-started]");
    expect(tmdbStart.text()).toBe("Doc TMDB ↗");
    expect(tmdbStart.attributes("href")).toBe("https://developer.themoviedb.org/docs/getting-started");
    expect(tmdbStart.attributes("target")).toBe("_blank");
    expect(tmdbStart.attributes("rel")).toBe("noreferrer noopener");

    const tmdbTokenDoc = wrapper.find("[data-testid=tmdb-doc-read-token]");
    expect(tmdbTokenDoc.text()).toBe("Jeton de lecture ↗");
    expect(tmdbTokenDoc.attributes("href")).toBe(
      "https://developer.themoviedb.org/docs/authentication-application",
    );
    expect(tmdbTokenDoc.attributes("target")).toBe("_blank");
    expect(tmdbTokenDoc.attributes("rel")).toBe("noreferrer noopener");

    const ghcrDoc = wrapper.find("[data-testid=github-doc-ghcr]");
    expect(ghcrDoc.text()).toBe("Doc GHCR ↗");
    expect(ghcrDoc.attributes("href")).toContain("container-registry");
    expect(ghcrDoc.attributes("target")).toBe("_blank");
    expect(ghcrDoc.attributes("rel")).toBe("noreferrer noopener");
    expect(wrapper.find("[data-testid=github-doc-pat]").text()).toBe("Doc jetons GitHub ↗");

    const watchtowerDoc = wrapper.find("[data-testid=watchtower-doc]");
    expect(watchtowerDoc.text()).toBe("Doc Watchtower ↗");
    expect(watchtowerDoc.attributes("href")).toBe("https://watchtower.nickfedor.com/");
    expect(watchtowerDoc.attributes("target")).toBe("_blank");
    expect(watchtowerDoc.attributes("rel")).toBe("noreferrer noopener");

    expect(wrapper.find("[data-testid=github-doc-pat]").exists()).toBe(true);
    expect(wrapper.find("[data-testid=watchtower-doc]").exists()).toBe(true);

    wrapper.unmount();
  });

  it("pastille Enregistré quand les secrets sont renseignés", async () => {
    serviceStatusRefsForTests.adConnected!.value = true;
    serviceStatusRefsForTests.jellyfinConfigured!.value = true;
    rows.value = [
      { key: "tmdb_read_token", value: null, secret: true, set: true },
      { key: "ghcr_read_token", value: null, secret: true, set: true },
      { key: "github_update_token", value: null, secret: true, set: false },
      { key: "watchtower_url", value: "http://watchtower:8080", secret: false, set: true },
      { key: "watchtower_token", value: null, secret: true, set: true },
      { key: "notify_on_update", value: false, secret: false, set: true },
    ];
    const wrapper = await mountJetons();
    expect(wrapper.find("[data-testid=tmdb-card-status]").text()).toBe("Enregistré");
    expect(wrapper.find("[data-testid=alldebrid-card-status]").text()).toBe("Connecté");
    expect(wrapper.find("[data-testid=jellyfin-card-status]").text()).toBe("Enregistré");
    expect(wrapper.find("[data-testid=github-card-status]").text()).toBe("Enregistré");
    expect(wrapper.find("[data-testid=watchtower-card-status]").text()).toBe("Enregistré");
    wrapper.unmount();
  });

  it("affiche le message d'enregistrement uniquement dans la carte concernée", async () => {
    const wrapper = await mountJetons();
    await wrapper.find("[data-testid=watchtower-url-input]").setValue("http://watchtower:8080");
    await wrapper.find("[data-testid=watchtower-token-input]").setValue("secret-value");
    await wrapper.find("[data-testid=outbound-secrets-form]").trigger("submit.prevent");
    await flushPromises();

    const githubCard = wrapper.find("[data-testid=service-card-github]");
    const watchtowerCard = wrapper.find("[data-testid=service-card-watchtower]");
    expect(watchtowerCard.find(".settings-saved").text()).toContain("Enregistré");
    expect(githubCard.find(".settings-saved").exists()).toBe(false);
    expect(githubCard.find(".error").exists()).toBe(false);
    wrapper.unmount();
  });

  it("envoie tmdb_read_token à l'enregistrement et affiche le message dans la carte TMDB", async () => {
    const wrapper = await mountJetons();
    await wrapper.find("[data-testid=tmdb-token-input]").setValue("demo-tmdb-token");
    await wrapper.find("[data-testid=tmdb-secrets-form]").trigger("submit.prevent");
    await flushPromises();

    expect(patchSettings).toHaveBeenCalledWith({ values: { tmdb_read_token: "demo-tmdb-token" } });
    const tmdbCard = wrapper.find("[data-testid=service-card-tmdb]");
    expect(tmdbCard.find(".settings-saved").text()).toContain("Enregistré");
    expect(wrapper.find("[data-testid=service-card-github]").find(".settings-saved").exists()).toBe(false);
    wrapper.unmount();
  });

  it("n'appelle pas patchSettings si le jeton TMDB est vide", async () => {
    const wrapper = await mountJetons();
    await wrapper.find("[data-testid=tmdb-secrets-form]").trigger("submit.prevent");
    await flushPromises();
    expect(patchSettings).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it("vide l'URL Jellyfin en envoyant jellyfin_url vide", async () => {
    rows.value = [
      { key: "tmdb_read_token", value: null, secret: true, set: false },
      { key: "ghcr_read_token", value: null, secret: true, set: false },
      { key: "github_update_token", value: null, secret: true, set: false },
      { key: "watchtower_url", value: "", secret: false, set: false },
      { key: "watchtower_token", value: null, secret: true, set: false },
      { key: "notify_on_update", value: false, secret: false, set: true },
      { key: "jellyfin_url", value: "http://jellyfin:8096", secret: false, set: true },
      { key: "jellyfin_api_key", value: null, secret: true, set: true },
    ];
    const wrapper = await mountJetons();
    await wrapper.find("[data-testid=jellyfin-url-input]").setValue("");
    await wrapper.find("[data-testid=jellyfin-secrets-form]").trigger("submit.prevent");
    await flushPromises();
    expect(patchSettings).toHaveBeenCalledWith({ values: { jellyfin_url: "" } });
    wrapper.unmount();
  });
});
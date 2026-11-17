import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api";
import { resetServiceStatusForTests } from "../../composables/useServiceStatus";
import SettingsSourcesSection from "./SettingsSourcesSection.vue";

const apiMock = vi.hoisted(() => vi.fn());
const fetchSettingsMock = vi.hoisted(() => vi.fn());
const patchSettingsMock = vi.hoisted(() => vi.fn());
const adConnectedFlag = vi.hoisted(() => ({ value: true }));

vi.mock("../../api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api")>();
  return {
    ...actual,
    api: apiMock,
  };
});
vi.mock("../../settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../settings")>();
  const { sanitizePatchValues } = await import("../../settingsMeta");
  return {
    ...actual,
    fetchSettings: fetchSettingsMock,
    patchSettings: async (body: { values: Record<string, unknown> }) => {
      return patchSettingsMock({ values: sanitizePatchValues(body.values) });
    },
  };
});
vi.mock("vue-router", () => ({
  RouterLink: { template: "<a><slot /></a>" },
}));

const defaultSources = [
  {
    source: "wawacity",
    label: "Wawacity",
    enabled: true,
    available: true,
    rank: 1,
    state: "ok",
    message: "",
    since: "2026-11-09T20:00:00Z",
    last_probe_at: "2026-11-09T20:30:00Z",
    last_probe_link_count: 2,
    last_probe_duration_ms: 400,
    last_probe_issue: "found",
    last_probe_reason: null,
    last_success_at: "2026-11-09T20:30:00Z",
  },
  {
    source: "comet",
    label: "Comet",
    enabled: true,
    available: true,
    rank: 2,
    state: "down",
    message: "Ancienne panne",
    since: "2026-11-09T18:00:00Z",
    last_probe_at: "2026-11-09T21:00:00Z",
    last_probe_link_count: 0,
    last_probe_duration_ms: 300,
    last_probe_issue: "empty",
    last_probe_reason: null,
    last_success_at: null,
  },
];

function mountSources() {
  return mount(SettingsSourcesSection, {
    global: {
      stubs: { SourceRank: true, CometInstanceList: true },
    },
  });
}

describe("SettingsSourcesSection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    resetServiceStatusForTests();
    adConnectedFlag.value = true;
    patchSettingsMock.mockResolvedValue({});
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "download_source_order", value: "wawacity,comet", secret: false, set: true }],
      source_defaults: {
        comet_base_urls: ["https://comet-defaut.example"],
        torrentio_base_url: "https://torrentio-defaut.example",
      },
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: false };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: defaultSources,
          available_sources: ["wawacity", "comet"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url.startsWith("/api/v1/providers/types")) {
        return { items: [] };
      }
      if (url === "/api/v1/settings/sources/hosters") {
        return { hosters: ["rapidgator", "1fichier"] };
      }
      return {};
    });
  });

  it("reste rendu après chargement du statut (dates ISO)", async () => {
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.text()).toContain("Sources de téléchargement");
    expect(wrapper.text()).toMatch(/En ligne depuis|Dernier test/);
  });

  it("accepte comet_base_urls en tableau depuis les réglages", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        {
          key: "comet_base_urls",
          value: ["https://instance-a.example", "https://instance-b.example"],
          secret: false,
          set: true,
        },
      ],
    });
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.text()).toContain("Sources de téléchargement");
  });

  it("affiche le bandeau quand aucune source n’est utilisable", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: defaultSources.map((row) => ({ ...row, available: false })),
          available_sources: [],
          download_ready: false,
          blocked_message: "Renseigne ta clé AllDebrid ou active Hive pour télécharger",
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url.startsWith("/api/v1/providers/types")) {
        return { items: [] };
      }
      if (url === "/api/v1/settings/sources/hosters") {
        return { hosters: ["rapidgator", "1fichier"] };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.text()).toContain("Renseigne ta clé AllDebrid");
    expect(wrapper.text()).toContain("Services et jetons");
    expect(wrapper.find(".source-order-panel").exists()).toBe(false);
  });

  it("affiche le résultat du dernier test sur la carte source", async () => {
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.text()).toMatch(/Dernier test.*trouvé 2 liens/);
    expect(wrapper.text()).toMatch(/Dernier test.*aucun résultat/);
  });

  it("« Tester toutes » appelle une sonde par source et continue après erreur", async () => {
    const probeCalls: string[][] = [];
    apiMock.mockImplementation(async (url: string, init?: { method?: string; body?: string }) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: defaultSources,
          available_sources: ["wawacity", "comet"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url === "/api/v1/settings/sources/probe" && init?.method === "POST") {
        const payload = JSON.parse(init.body || "{}") as { sources?: string[] };
        probeCalls.push(payload.sources ?? []);
        const source = payload.sources?.[0] ?? "unknown";
        if (source === "wawacity") {
          throw new Error("réseau simulé");
        }
        return {
          results: [
            {
              source,
              label: source,
              issue: "empty",
              link_count: 0,
              reason: null,
              duration_ms: 50,
            },
          ],
        };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    const query = wrapper.find('input[placeholder*="Titre fictif"]');
    await query.setValue("Film Démo");
    await wrapper.find("button.primary").trigger("click");
    await flushPromises();
    expect(probeCalls).toEqual([["wawacity"], ["comet"]]);
    expect(wrapper.text()).toContain("Test impossible.");
    expect(wrapper.text()).toContain("aucune source");
  });

  it("affiche Désactivée sur l’interrupteur et le statut quand la source est hors ordre", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: [
            ...defaultSources,
            {
              source: "free_telecharger",
              label: "Free-Télécharger",
              enabled: false,
              available: true,
              rank: 0,
              state: "ok",
              message: "",
              since: "2026-11-09T20:00:00Z",
              last_probe_at: null,
              last_probe_link_count: null,
              last_probe_duration_ms: null,
              last_probe_issue: null,
              last_probe_reason: null,
              last_success_at: null,
            },
          ],
          available_sources: ["wawacity", "comet", "free_telecharger"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url.startsWith("/api/v1/providers/types")) {
        return { items: [] };
      }
      if (url === "/api/v1/settings/sources/hosters") {
        return { hosters: ["rapidgator", "1fichier"] };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    const card = wrapper.find(".source-card.is-off");
    expect(card.exists()).toBe(true);
    expect(card.text()).toContain("Free-Télécharger");
    expect(card.text()).not.toContain("free_telecharger");
    expect(card.text()).toContain("Désactivée");
    expect(card.text()).not.toContain("En ligne depuis");
  });

  it("expose un libellé lisible pour les addons Stremio", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "download_source_order", value: "stremio_addons", secret: false, set: true },
      ],
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: [
            {
              source: "stremio_addons",
              label: "Addons Stremio",
              enabled: true,
              available: true,
              rank: 1,
              state: "ok",
              message: "",
              since: "2026-11-09T20:00:00Z",
              last_probe_at: null,
              last_probe_link_count: null,
              last_probe_duration_ms: null,
              last_probe_issue: null,
              last_probe_reason: null,
              last_success_at: null,
            },
          ],
          available_sources: ["stremio_addons"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url.startsWith("/api/v1/providers/types")) {
        return { items: [] };
      }
      if (url === "/api/v1/settings/sources/hosters") {
        return { hosters: ["rapidgator", "1fichier"] };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.text()).toContain("Addons Stremio");
    expect(wrapper.text()).not.toContain("stremio_addons");
  });

  it("affiche le détail hébergeur renvoyé par l’API à l’enregistrement", async () => {
    patchSettingsMock.mockRejectedValue(
      new ApiError(422, "Données invalides.", undefined, undefined, {
        detail: "Hébergeur inconnu : exempleshare.",
      }),
    );
    const wrapper = mountSources();
    await flushPromises();
    const saveRhythm = wrapper
      .findAll(".source-rhythm-panel button")
      .find((btn) => btn.text() === "Enregistrer le rythme");
    expect(saveRhythm).toBeTruthy();
    await saveRhythm!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Hébergeur inconnu : exempleshare.");
    expect(wrapper.text()).not.toContain("Enregistrement impossible.");
  });

  it("tolère sources null sur l’endpoint statut", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/settings/sources/status") {
        return { sources: null, available_sources: [], download_ready: true };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url.startsWith("/api/v1/providers/types")) {
        return { items: [] };
      }
      if (url === "/api/v1/settings/sources/hosters") {
        return { hosters: ["rapidgator", "1fichier"] };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.text()).toContain("Ordre des sources");
  });

  it("conserve les sources masquées dans download_source_order à l’enregistrement", async () => {
    adConnectedFlag.value = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "download_source_order", value: "wawacity,comet,hive", secret: false, set: true },
        { key: "hoster_preference", value: "rapidgator,1fichier", secret: false, set: true },
      ],
      source_defaults: {
        comet_base_urls: ["https://comet-defaut.example"],
        torrentio_base_url: "https://torrentio-defaut.example",
      },
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: [
            { ...defaultSources[0], available: false },
            { ...defaultSources[1], available: true },
            {
              source: "hive",
              label: "Hive",
              enabled: true,
              available: true,
              rank: 3,
              state: "ok",
              message: "",
              since: "2026-11-09T20:00:00Z",
              last_probe_at: null,
              last_probe_link_count: null,
              last_probe_duration_ms: null,
              last_probe_issue: null,
              last_probe_reason: null,
              last_success_at: null,
            },
          ],
          available_sources: ["wawacity", "comet", "hive"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    await wrapper.find(".source-order-panel button").trigger("click");
    await flushPromises();
    const patch = patchSettingsMock.mock.calls[0]?.[0] as { values: Record<string, string> };
    expect(patch.values.download_source_order).toContain("wawacity");
    expect(patch.values.hoster_preference).toBe("rapidgator,1fichier");
  });

  it("masque le rythme et affiche le lien jetons sans AllDebrid connecté", async () => {
    adConnectedFlag.value = false;
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: [
            { ...defaultSources[0], available: false },
            { ...defaultSources[1], available: true },
          ],
          available_sources: ["wawacity", "comet"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      if (url.startsWith("/api/v1/providers/types")) {
        return { items: [] };
      }
      if (url === "/api/v1/settings/sources/hosters") {
        return { hosters: ["rapidgator", "1fichier"] };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.find(".source-rhythm-panel").exists()).toBe(false);
    expect(wrapper.text()).toContain("Disponible après connexion d'AllDebrid");
    expect(wrapper.text()).toContain("Services et jetons");
    expect(wrapper.text()).toContain("Comet");
    expect(wrapper.text()).not.toContain("Wawacity");
  });

  it("n’affiche pas le masquage AllDebrid avant la fin du chargement service", async () => {
    let finishAd: (value: { connected: boolean }) => void = () => undefined;
    const adPending = new Promise<{ connected: boolean }>((resolve) => {
      finishAd = resolve;
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return adPending;
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: defaultSources,
          available_sources: ["wawacity", "comet"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    expect(wrapper.find(".source-rhythm-panel").exists()).toBe(true);
    expect(wrapper.text()).not.toContain("Disponible après connexion");
    finishAd({ connected: false });
    await flushPromises();
  });

  it("ajoute une source réactivée à download_source_order", async () => {
    fetchSettingsMock.mockResolvedValue({
      settings: [{ key: "download_source_order", value: "comet", secret: false, set: true }],
      source_defaults: {
        comet_base_urls: ["https://comet-defaut.example"],
        torrentio_base_url: "https://torrentio-defaut.example",
      },
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: adConnectedFlag.value };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: false };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: [
            ...defaultSources,
            {
              source: "free_telecharger",
              label: "Free-Télécharger",
              enabled: false,
              available: true,
              rank: 0,
              state: "ok",
              message: "",
              since: null,
              last_probe_at: null,
              last_probe_link_count: null,
              last_probe_duration_ms: null,
              last_probe_issue: null,
              last_probe_reason: null,
              last_success_at: null,
            },
          ],
          available_sources: ["wawacity", "comet", "free_telecharger"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    const ftCard = wrapper.findAll(".source-card").find((card) => card.text().includes("Free-Télécharger"));
    expect(ftCard).toBeTruthy();
    expect(ftCard!.classes()).toContain("is-off");
    const checkbox = ftCard!.find('input[type="checkbox"]');
    expect((checkbox.element as HTMLInputElement).checked).toBe(false);
    await checkbox.setValue(true);
    await flushPromises();
    const order = patchSettingsMock.mock.calls.at(-1)?.[0] as { values: Record<string, string> };
    expect(order.values.download_source_order).toContain("free_telecharger");
    expect(order.values.download_source_order).toContain("comet");
  });

  it("désactive une source sans retirer les jetons masqués", async () => {
    adConnectedFlag.value = false;
    fetchSettingsMock.mockResolvedValue({
      settings: [
        { key: "download_source_order", value: "wawacity,comet,hive", secret: false, set: true },
        { key: "hive_enabled", value: true, secret: false, set: true },
      ],
      source_defaults: {
        comet_base_urls: ["https://comet-defaut.example"],
        torrentio_base_url: "https://torrentio-defaut.example",
      },
    });
    apiMock.mockImplementation(async (url: string) => {
      if (url === "/api/v1/integrations/alldebrid/account") {
        return { connected: false };
      }
      if (url === "/api/v1/ha/update") {
        return { watchtower_configured: false };
      }
      if (url === "/api/v1/settings/sources/status") {
        return {
          sources: [
            { ...defaultSources[0], available: false },
            { ...defaultSources[1], available: true },
            {
              source: "hive",
              label: "Hive",
              enabled: true,
              available: true,
              rank: 3,
              state: "ok",
              message: "",
              since: null,
              last_probe_at: null,
              last_probe_link_count: null,
              last_probe_duration_ms: null,
              last_probe_issue: null,
              last_probe_reason: null,
              last_success_at: null,
            },
          ],
          available_sources: ["wawacity", "comet", "hive"],
          download_ready: true,
          blocked_message: null,
        };
      }
      if (url === "/api/v1/providers?kind=source") {
        return { items: [] };
      }
      if (url === "/api/v1/hive/ruches") {
        return [];
      }
      if (url === "/api/v1/wawacity/domain") {
        return { current: null, candidate: null, previous: null };
      }
      return {};
    });
    const wrapper = mountSources();
    await flushPromises();
    const cometCard = wrapper.findAll(".source-card").find((card) => card.text().includes("Comet"));
    expect(cometCard).toBeTruthy();
    const cometToggle = cometCard!.find('input[type="checkbox"]');
    await cometToggle.setValue(false);
    await flushPromises();
    const saved = patchSettingsMock.mock.calls.at(-1)?.[0] as { values: Record<string, string> };
    expect(saved.values.download_source_order).toContain("wawacity");
    expect(saved.values.download_source_order).toContain("hive");
    expect(saved.values.download_source_order).not.toContain("comet");
  });

  it("aligne le recul après erreur sur les bornes serveur (30–1440)", async () => {
    const wrapper = mountSources();
    await flushPromises();
    const label = wrapper.findAll("label").find((row) => row.text().includes("Recul après erreur"));
    expect(label).toBeTruthy();
    const input = label!.find('input[type="number"]');
    expect(input.attributes("min")).toBe("30");
    expect(input.attributes("max")).toBe("1440");
  });

  it("n’envoie pas null ni NaN pour le rythme si l’enregistrement part avant le chargement", async () => {
    let finishLoad: ((page: unknown) => void) | undefined;
    const pendingLoad = new Promise<unknown>((resolve) => {
      finishLoad = resolve;
    });
    fetchSettingsMock.mockReturnValue(pendingLoad);
    const wrapper = mountSources();
    const rhythmBtn = wrapper
      .findAll("button")
      .find((btn) => btn.text().includes("Enregistrer le rythme"));
    expect(rhythmBtn).toBeTruthy();
    await rhythmBtn!.trigger("click");
    await flushPromises();
    expect(patchSettingsMock).toHaveBeenCalled();
    const values = (patchSettingsMock.mock.calls.at(-1)?.[0] as { values: Record<string, unknown> }).values;
    for (const [, value] of Object.entries(values)) {
      expect(value).not.toBeNull();
      if (typeof value === "number") {
        expect(Number.isFinite(value)).toBe(true);
      }
    }
    expect(values.source_request_delay_seconds).toBeUndefined();
    expect(values.source_cache_seconds).toBeUndefined();
    expect(values.source_error_backoff_min).toBeUndefined();
    expect(values.remote_search_cooldown_hours).toBeUndefined();
    finishLoad?.({
      settings: [{ key: "download_source_order", value: "wawacity", secret: false, set: true }],
      source_defaults: { comet_base_urls: [], torrentio_base_url: "https://torrentio.example" },
    });
    await flushPromises();
  });
});

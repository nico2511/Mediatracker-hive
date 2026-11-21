import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import HealthView from "./HealthView.vue";

const apiMock = vi.fn(async (url: string, options?: { method?: string }) => {
  if (url === "/api/v1/health/components") {
    return {
      status: "ok",
      components: [
        { name: "aria2", status: "ok", message: "aria2 répond." },
        { name: "library", status: "ok", message: "Bibliothèque accessible." },
        { name: "disk", status: "ok", message: "10 Gio libres." },
        { name: "alldebrid", status: "skipped", message: "Clé absente." },
        { name: "tmdb", status: "skipped", message: "Jeton absent." },
        { name: "jellyfin", status: "skipped", message: "Non configuré." },
        { name: "wawacity", status: "skipped", message: "Domaine absent." },
        { name: "notifier", status: "skipped", message: "Aucune cible." },
        { name: "volumes", status: "ok", message: "OK." },
      ],
    };
  }
  if (url.startsWith("/api/v1/health/history")) {
    return { since: "2026-01-01T00:00:00Z", until: "2026-01-08T00:00:00Z", bucket_minutes: 60, points: [] };
  }
  if (url === "/api/v1/health/metrics") {
    return {
      sampled_at: 1_700_000_000,
      interval_s: 5,
      instant: { processes: {}, aria2: {}, worker_wakes: {}, task_durations: {}, source_requests: {} },
      series: [{ ts: 1_700_000_000, processes: {}, aria2: {} }],
      http: {},
    };
  }
  if (url === "/api/v1/dashboard") {
    return { downloads_active: [{}], downloads_failed: [] };
  }
  if (url === "/api/v1/health/disk") {
    return { volumes: [{ total_bytes: 100, used_bytes: 48 }] };
  }
  if (url === "/api/v1/health/sources") {
    return {
      targets: [{ key: "src-comet", kind: "source", label: "Comet", state: "ok", message: "", since: "2026-01-01T00:00:00Z" }],
    };
  }
  if (url === "/api/v1/ha/update") {
    return {
      detail: "À jour.",
      update_available: false,
      watchtower_configured: true,
      checked_at: "2026-01-01T08:00:00Z",
    };
  }
  if (url === "/api/v1/ha/actions/check-update" && options?.method === "POST") {
    return { detail: "À jour.", update_available: false };
  }
  throw new Error(`unexpected ${url}`);
});

vi.mock("../api", () => ({
  api: (...args: unknown[]) => apiMock(...args),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: { value: false }, refreshHive: async () => {} }),
}));

describe("HealthView", () => {
  it("affiche le bandeau et Bibliothèque dans la liste", async () => {
    const wrapper = mount(HealthView, {
      global: {
        stubs: {
          SourceStatesCard: true,
          DiskSpaceCard: true,
          HealthHiveRucheCard: true,
          HealthSeriesChart: true,
          MetricSparkline: true,
          RouterLink: { template: "<a><slot /></a>" },
        },
      },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Tout va bien");
    expect(wrapper.text()).toContain("Bibliothèque");
    expect(wrapper.text()).toContain("Mesures");
    expect(apiMock).toHaveBeenCalledWith("/api/v1/health/sources");
  });

  it("ne charge l'historique 7 jours qu'à l'ouverture des Détails techniques", async () => {
    apiMock.mockClear();
    const wrapper = mount(HealthView, {
      global: {
        stubs: {
          SourceStatesCard: true,
          DiskSpaceCard: true,
          HealthHiveRucheCard: true,
          HealthSeriesChart: true,
          MetricSparkline: true,
          RouterLink: { template: "<a><slot /></a>" },
        },
      },
    });
    await flushPromises();
    const historyCalls = () => apiMock.mock.calls.filter(([url]) => String(url).startsWith("/api/v1/health/history"));
    expect(historyCalls()).toHaveLength(0);
    const tech = wrapper.find(".health-tech-details");
    (tech.element as HTMLDetailsElement).open = true;
    await tech.trigger("toggle");
    await flushPromises();
    expect(historyCalls()).toHaveLength(1);
  });
});

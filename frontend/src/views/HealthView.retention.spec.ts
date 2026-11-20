import { mount, flushPromises } from "@vue/test-utils";
import { nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setFixedRetentionForTests } from "../fixedRetention";
import HealthView from "./HealthView.vue";

vi.mock("../api", () => ({
  api: vi.fn(async (url: string) => {
    if (url.startsWith("/api/v1/health/history")) {
      return { since: "2026-01-01T00:00:00Z", until: "2026-01-08T00:00:00Z", bucket_minutes: 60, points: [] };
    }
    if (url === "/api/v1/health/components") {
      return { status: "ok", components: [] };
    }
    if (url === "/api/v1/health/metrics") {
      return { sampled_at: 0, interval_s: 5, instant: { processes: {}, aria2: {} }, series: [], http: {} };
    }
    if (url === "/api/v1/dashboard") {
      return { downloads_active: [], downloads_failed: [] };
    }
    if (url === "/api/v1/health/disk") {
      return { volumes: [] };
    }
    throw new Error(`unexpected ${url}`);
  }),
  userFacingApiError: (_e: unknown, fallback: string) => fallback,
}));

vi.mock("../composables/useHive", () => ({
  useHiveEnabled: () => ({ hiveEnabled: { value: false }, refreshHive: async () => {} }),
}));

describe("HealthView retention label — chargement tardif", () => {
  it("met à jour le titre après chargement tardif du cache", async () => {
    setFixedRetentionForTests(null);
    const wrapper = mount(HealthView, {
      global: { stubs: { SourceStatesCard: true, DiskSpaceCard: true, HealthHiveRucheCard: true, HealthSeriesChart: true, MetricSparkline: true } },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Historique");
    expect(wrapper.text()).not.toContain("Historique (");
    setFixedRetentionForTests({
      event_journal_days: 30,
      source_state_days: 30,
      metrics_history_days: 12,
      incomplete_orphan_keep_days: 3,
    });
    await nextTick();
    expect(wrapper.text()).toContain("Historique (12 jours)");
  });
});

describe("HealthView retention label", () => {
  beforeEach(() => {
    setFixedRetentionForTests({
      event_journal_days: 30,
      source_state_days: 30,
      metrics_history_days: 9,
      incomplete_orphan_keep_days: 3,
    });
  });

  it("affiche la durée d’historique métriques depuis l’API", async () => {
    const wrapper = mount(HealthView, {
      global: { stubs: { SourceStatesCard: true, DiskSpaceCard: true, HealthHiveRucheCard: true, HealthSeriesChart: true, MetricSparkline: true } },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("Historique (9 jours)");
  });
});
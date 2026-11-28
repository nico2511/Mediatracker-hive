import { mount, flushPromises } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";
import { createRouter, createWebHistory } from "vue-router";

import HealthActivityView from "./HealthActivityView.vue";

const sampleBody = {
  hours: 24,
  since: "2026-11-26T10:00:00Z",
  until: "2026-11-27T10:00:00Z",
  has_data: true,
  collection: { active: true, until: null, mode: "unlimited", duration: "unlimited" },
  kpis: {
    trains_in_progress: 2,
    active_blocks: 0,
    slow_stations: 0,
    network_anomalies: 0,
    edge_passes_24h: 10,
    median_pass_s: 1.2,
    untraceable_links: 6,
  },
  active_blocks: [],
  slow_stations: [],
  station_stats: {},
  edge_hits: {},
  lit_edges: [],
  lit_pass_edge: null,
  lit_block_edges: [],
  level1_edges: [{ from: "Rangement", to: "Telechargements", count: 4, count_label: "4" }],
  edge_details: {},
  rng5_exit_stats: {},
  station_details: {
    RNG5: {
      outbound: [
        {
          edge_id: "C16",
          kind: "declenche",
          dest_label: "Liaison médias",
          dest_line: "Jellyfin",
          passes_24h: 2,
          lit: true,
          untraceable: false,
        },
      ],
    },
  },
  graph: {
    view_box: { width: 930, height: 800 },
    station_count_total: 85,
    station_count_visible: 33,
    edge_count_visible: 33,
    untraceable_count: 6,
    lines: [{ number: 5, name: "Rangement", color: "#CF009E", x: 640, y: 390 }],
    stations: [
      {
        id: "RNG5",
        line: "Rangement",
        line_number: 5,
        label: "Finalisation",
        color: "#CF009E",
        x: 670,
        y: 360,
        instrumented: true,
      },
    ],
    edges: [],
    diagnostics: [],
    rng5_mechanisms: [],
    rng5_exits: [],
    peer_off_plan: { x: 890, y: 640, label: "Pair (hors plan)" },
    ruche_ring: { cx: 800, cy: 465, r: 75 },
    retention_days: 7,
  },
};

vi.mock("../api", () => ({
  api: vi.fn(async () => sampleBody),
  userFacingApiError: (_err: unknown, fallback: string) => fallback,
}));

describe("HealthActivityView", () => {
  it("affiche les KPI quand la collecte a des données", async () => {
    const router = createRouter({
      history: createWebHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    const wrapper = mount(HealthActivityView, {
      global: { plugins: [router] },
    });
    await flushPromises();
    expect(wrapper.find("[data-testid='health-activity']").exists()).toBe(true);
    expect(wrapper.text()).toContain("rames en cours");
    expect(wrapper.text()).toContain("Finalisation");
  });

  it("affiche l'état vide sans collecte", async () => {
    const { api } = await import("../api");
    vi.mocked(api).mockResolvedValueOnce({
      ...sampleBody,
      has_data: false,
      collection: { active: false, until: null, mode: "off", duration: "off" },
    });
    const router = createRouter({
      history: createWebHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    const wrapper = mount(HealthActivityView, {
      global: { plugins: [router] },
    });
    await flushPromises();
    expect(wrapper.text()).toContain("collecte des passages");
    expect(wrapper.find("a[href='/reglages/application']").exists()).toBe(true);
  });
});
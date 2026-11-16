import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import HealthHiveRucheCard from "./HealthHiveRucheCard.vue";
import type { HivePeerLoadResponse } from "../hive/hivePeerLoad";

const self = {
  instance_id: "inst-local",
  out_active: 2,
  parallel_limit: 4,
  send_cap_bps: 12_000_000,
  send_avail_bps: 5_000_000,
  busy: false,
  snapshot_at: "2099-06-01T12:00:00Z",
};

const payload: HivePeerLoadResponse = {
  self,
  items: [
    {
      ruche_id_hex: "a1b2c3d4".repeat(8),
      origin_instance_id: "inst-peer-a",
      out_active: 1,
      parallel_limit: 4,
      send_cap_bps: 20_000_000,
      send_avail_bps: 8_000_000,
      busy: false,
      snapshot_at: "2099-06-01T12:00:00Z",
      received_at: "2099-06-01T11:59:50Z",
      stale: false,
    },
    {
      ruche_id_hex: "a1b2c3d4".repeat(8),
      origin_instance_id: "inst-peer-stale",
      out_active: 0,
      parallel_limit: 4,
      send_cap_bps: 0,
      send_avail_bps: null,
      busy: false,
      snapshot_at: "2099-06-01T10:00:00Z",
      received_at: "2099-06-01T10:00:00Z",
      stale: true,
    },
  ],
};

describe("HealthHiveRucheCard", () => {
  it("affiche la synthèse et les pairs", () => {
    const wrapper = mount(HealthHiveRucheCard, { props: { payload } });
    const root = wrapper.get("[data-testid=health-hive-ruche]");
    expect(root.text()).toContain("Mes envois");
    expect(root.text()).toContain("2/4");
    expect(root.text()).toContain("inst-peer-a".slice(-8));
    expect(root.text()).toContain("périmé");
    expect(wrapper.find(".health-hive-peer-stale").exists()).toBe(true);
  });

  it("affiche l'état vide sans pairs distants", () => {
    const wrapper = mount(HealthHiveRucheCard, {
      props: { payload: { self, items: [] } },
    });
    expect(wrapper.text()).toContain("Aucun pair connu");
  });

  it("affiche une erreur", () => {
    const wrapper = mount(HealthHiveRucheCard, {
      props: { payload: null, error: "Charge indisponible" },
    });
    expect(wrapper.text()).toContain("Charge indisponible");
  });
});
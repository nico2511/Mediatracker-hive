import { describe, expect, it } from "vitest";

import {
  formatAgeFr,
  groupPeersByRuche,
  outboundBps,
  peerReachability,
  summarizePeerLoad,
} from "./hivePeerLoad";

const basePeer = {
  ruche_id_hex: "aabbccdd".repeat(8),
  origin_instance_id: "inst-alpha",
  out_active: 1,
  parallel_limit: 4,
  send_cap_bps: 20_000_000,
  send_avail_bps: 8_000_000,
  busy: false,
  snapshot_at: "2099-01-01T12:00:00Z",
  received_at: "2099-01-01T12:00:00Z",
  stale: false,
};

describe("hivePeerLoad", () => {
  it("calcule le débit sortant à partir du plafond et du disponible", () => {
    expect(outboundBps(20_000_000, 8_000_000)).toBe(12_000_000);
    expect(outboundBps(20_000_000, null)).toBeNull();
  });

  it("classe les pairs selon busy et stale", () => {
    expect(peerReachability(basePeer)).toBe("libre");
    expect(peerReachability({ ...basePeer, busy: true })).toBe("occupe");
    expect(peerReachability({ ...basePeer, stale: true })).toBe("inconnu");
  });

  it("résume les compteurs en excluant l'instance locale", () => {
    const self = {
      instance_id: "inst-local",
      out_active: 2,
      parallel_limit: 4,
      send_cap_bps: 10_000_000,
      send_avail_bps: 3_200_000,
      busy: false,
      snapshot_at: "2099-01-01T12:00:00Z",
    };
    const items = [
      basePeer,
      { ...basePeer, origin_instance_id: "inst-beta", busy: true },
      { ...basePeer, origin_instance_id: "inst-gamma", stale: true },
      { ...basePeer, origin_instance_id: "inst-local" },
    ];
    const summary = summarizePeerLoad(self, items, "inst-local");
    expect(summary.sendsLabel).toBe("2/4");
    expect(summary.reachable).toBe(2);
    expect(summary.busy).toBe(1);
    expect(summary.unknown).toBe(1);
    expect(summary.outboundLabel).toContain("/s");
  });

  it("groupe les pairs par ruche", () => {
    const otherRuche = "11223344".repeat(8);
    const grouped = groupPeersByRuche(
      [
        basePeer,
        { ...basePeer, origin_instance_id: "inst-beta" },
        { ...basePeer, ruche_id_hex: otherRuche, origin_instance_id: "inst-other" },
      ],
      null,
    );
    expect(grouped).toHaveLength(2);
    expect(grouped[0]?.peers.length).toBeGreaterThan(0);
  });

  it("formate l'âge relatif en français", () => {
    const now = Date.parse("2099-01-01T12:01:00Z");
    expect(formatAgeFr("2099-01-01T12:00:30Z", now)).toBe("il y a 30 s");
    expect(formatAgeFr("2099-01-01T11:59:00Z", now)).toBe("il y a 2 min");
  });
});
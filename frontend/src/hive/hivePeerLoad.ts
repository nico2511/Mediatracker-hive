import { formatBytes } from "../diskFormat";

export type HivePeerLoadItem = {
  ruche_id_hex: string;
  origin_instance_id: string;
  out_active: number;
  parallel_limit: number;
  send_cap_bps: number;
  send_avail_bps: number | null;
  busy: boolean;
  snapshot_at: string;
  received_at: string;
  stale: boolean;
};

export type HivePeerLoadSelf = {
  instance_id: string;
  out_active: number;
  parallel_limit: number;
  send_cap_bps: number;
  send_avail_bps: number | null;
  busy: boolean;
  snapshot_at: string;
};

export type HivePeerLoadResponse = {
  self: HivePeerLoadSelf | null;
  items: HivePeerLoadItem[];
};

export type PeerReachability = "libre" | "occupe" | "inconnu";

export function peerReachability(peer: HivePeerLoadItem): PeerReachability {
  if (peer.stale) {
    return "inconnu";
  }
  return peer.busy ? "occupe" : "libre";
}

export function peerReachabilityLabel(kind: PeerReachability): string {
  if (kind === "libre") {
    return "Libre";
  }
  if (kind === "occupe") {
    return "Occupé";
  }
  return "Inconnu";
}

export function formatBpsPerSecond(bps: number | null): string {
  if (bps == null || !Number.isFinite(bps)) {
    return "—";
  }
  return `${formatBytes(bps)}/s`;
}

export function outboundBps(sendCap: number, sendAvail: number | null): number | null {
  if (sendAvail == null || !Number.isFinite(sendAvail)) {
    return null;
  }
  return Math.max(0, sendCap - sendAvail);
}

export function formatAgeFr(iso: string, nowMs = Date.now()): string {
  const then = Date.parse(iso.includes("Z") || /[+-]\d{2}:\d{2}$/.test(iso) ? iso : `${iso}Z`);
  if (!Number.isFinite(then)) {
    return "—";
  }
  const seconds = Math.max(0, Math.round((nowMs - then) / 1000));
  if (seconds < 60) {
    return `il y a ${seconds} s`;
  }
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) {
    return `il y a ${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 48) {
    return `il y a ${hours} h`;
  }
  const days = Math.floor(hours / 24);
  return `il y a ${days} j`;
}

export function rucheGroupLabel(rucheIdHex: string): string {
  const short = rucheIdHex.slice(0, 4).toUpperCase();
  return `Ruche ${short}`;
}

export function peerInstanceLabel(instanceId: string, localId: string | null): string {
  if (localId && instanceId === localId) {
    return "Cette instance";
  }
  const tail = instanceId.slice(-8);
  return `Instance ${tail}`;
}

export function loadRatioLabel(outActive: number, parallelLimit: number, stale: boolean): string {
  if (stale || parallelLimit <= 0) {
    return "—";
  }
  return `${outActive}/${parallelLimit}`;
}

export function loadBarPercent(outActive: number, parallelLimit: number): number {
  if (parallelLimit <= 0) {
    return 0;
  }
  return Math.min(100, Math.round((outActive / parallelLimit) * 100));
}

export type PeerLoadSummary = {
  sendsLabel: string;
  outboundLabel: string;
  reachable: number;
  busy: number;
  unknown: number;
};

export function summarizePeerLoad(
  self: HivePeerLoadSelf | null,
  items: HivePeerLoadItem[],
  localId: string | null,
): PeerLoadSummary {
  const remote = localId ? items.filter((row) => row.origin_instance_id !== localId) : items;
  let reachable = 0;
  let busy = 0;
  let unknown = 0;
  for (const peer of remote) {
    const kind = peerReachability(peer);
    if (kind === "inconnu") {
      unknown += 1;
    } else if (kind === "occupe") {
      busy += 1;
      reachable += 1;
    } else {
      reachable += 1;
    }
  }
  const sendsLabel =
    self && self.parallel_limit > 0 ? `${self.out_active}/${self.parallel_limit}` : "—";
  const outbound = self ? outboundBps(self.send_cap_bps, self.send_avail_bps) : null;
  return {
    sendsLabel,
    outboundLabel: formatBpsPerSecond(outbound),
    reachable,
    busy,
    unknown,
  };
}

export type RuchePeerGroup = {
  rucheIdHex: string;
  label: string;
  peers: HivePeerLoadItem[];
};

export function groupPeersByRuche(items: HivePeerLoadItem[], localId: string | null): RuchePeerGroup[] {
  const remote = localId ? items.filter((row) => row.origin_instance_id !== localId) : items;
  const byRuche = new Map<string, HivePeerLoadItem[]>();
  for (const peer of remote) {
    const list = byRuche.get(peer.ruche_id_hex) ?? [];
    list.push(peer);
    byRuche.set(peer.ruche_id_hex, list);
  }
  const groups: RuchePeerGroup[] = [];
  for (const [rucheIdHex, peers] of byRuche) {
    peers.sort((a, b) => a.origin_instance_id.localeCompare(b.origin_instance_id));
    groups.push({
      rucheIdHex,
      label: rucheGroupLabel(rucheIdHex),
      peers,
    });
  }
  groups.sort((a, b) => a.label.localeCompare(b.label));
  return groups;
}
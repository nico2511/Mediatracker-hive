/** Fenêtre gossip worker par défaut (600 ± 120 s) lorsque last_gossip_at est absent. */
export const HIVE_GOSSIP_DEFAULT_WINDOW_MS = 600_000;

export function parseIsoUtc(iso: string | null | undefined): number | null {
  if (!iso || !iso.trim()) {
    return null;
  }
  const ms = Date.parse(iso);
  return Number.isFinite(ms) ? ms : null;
}

export function hiveGossipCountdownLabel(remainingMs: number): string {
  if (remainingMs <= 0) {
    return "imminente";
  }
  if (remainingMs < 60_000) {
    return "imminente";
  }
  const minutes = Math.ceil(remainingMs / 60_000);
  return `dans ${minutes} min`;
}

export function hiveGossipProgressFraction(
  nowMs: number,
  nextMs: number | null,
  lastMs: number | null,
): number {
  if (nextMs == null) {
    return 0;
  }
  const remaining = nextMs - nowMs;
  if (remaining <= 0) {
    return 1;
  }
  let start = lastMs;
  if (start == null || start >= nextMs) {
    start = nextMs - HIVE_GOSSIP_DEFAULT_WINDOW_MS;
  }
  const span = nextMs - start;
  if (span <= 0) {
    return 0;
  }
  const elapsed = nowMs - start;
  return Math.min(1, Math.max(0, elapsed / span));
}
/** Priorité pastille catalogue Hive (alignée README maquettes). */

export type HiveCatalogueCardTone = "danger" | "honey" | "muted" | "info" | "warn" | "ok";

export interface HiveCatalogueCardState {
  card_state: string;
  state_label: string;
  state_label_short: string;
  progress_pct: number | null;
  error_line: string | null;
  followed: boolean;
  shared_by_me: boolean;
  on_disk: boolean;
  tone: HiveCatalogueCardTone;
}

const TONE_BY_STATE: Record<string, HiveCatalogueCardTone> = {
  error: "danger",
  transfer: "honey",
  preparing: "muted",
  library: "info",
  waiting: "warn",
  available: "ok",
  unavailable: "muted",
};

export function hiveCatalogueCardTone(cardState: string): HiveCatalogueCardTone {
  return TONE_BY_STATE[cardState] ?? "muted";
}

export function hiveCatalogueStateLabel(
  row: Pick<HiveCatalogueCardState, "state_label" | "state_label_short" | "card_state" | "progress_pct">,
  compact: boolean,
): string {
  if (compact) {
    if (row.card_state === "transfer" && row.progress_pct != null) {
      return `Transfert ${Math.round(row.progress_pct)} %`;
    }
    if (row.card_state === "preparing" && row.progress_pct != null) {
      return `Prép. ${Math.round(row.progress_pct)} %`;
    }
    return row.state_label_short || row.state_label;
  }
  if (row.card_state === "transfer" && row.progress_pct != null) {
    return `Transfert ${Math.round(row.progress_pct)} %`;
  }
  if (row.card_state === "preparing" && row.progress_pct != null) {
    return `Préparation ${Math.round(row.progress_pct)} %`;
  }
  return row.state_label;
}

export function hiveCatalogueShowProgressBar(cardState: string, progressPct: number | null): boolean {
  return progressPct != null && (cardState === "transfer" || cardState === "preparing");
}

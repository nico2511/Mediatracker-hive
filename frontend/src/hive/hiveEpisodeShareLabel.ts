import type { HiveShareUiState } from "./hiveBadges";
import { hiveShareFicheStatus } from "./hiveShareStatus";

export interface HiveEpisodeShareChip {
  label: string;
  tone: "honey" | "muted" | "danger" | "info";
}

export function hiveEpisodeShareChip(state: HiveShareUiState | null | undefined): HiveEpisodeShareChip | null {
  if (!state?.has_file) {
    return null;
  }
  const status = hiveShareFicheStatus(state, {});
  if (status.phase === "error") {
    return { label: "Erreur", tone: "danger" };
  }
  if (status.phase === "preparing") {
    const partial = state.fingerprint_partial_size ?? 0;
    const total = state.fingerprint_total_size ?? 0;
    if (total > 0) {
      const pct =
        partial >= total ? 99 : Math.min(99, Math.max(0, Math.round((100 * partial) / total)));
      return { label: `Empreinte ${pct} %`, tone: "honey" };
    }
    return { label: "Préparation", tone: "muted" };
  }
  if (state.shared && !state.exclude_all) {
    return { label: "Partagé", tone: "honey" };
  }
  if (status.phase === "not_shared" || status.phase === "intention") {
    return { label: "Non partagé", tone: "muted" };
  }
  return null;
}

export interface HiveShareUiState {
  shareable: boolean;
  shared: boolean;
  exclude_all: boolean;
  excluded_ruche_ids: string[];
  preparing: boolean;
  transfer_active: boolean;
  transfer_progress_pct: number | null;
  transfer_outbound_active?: boolean;
  transfer_outbound_progress_pct?: number | null;
  received_via_hive?: boolean;
  fingerprint_partial_size?: number | null;
  fingerprint_total_size?: number | null;
  hive_keep: boolean;
  keep: boolean;
  pull_only: boolean;
  has_file: boolean;
  catalog_entry_local: boolean;
}

export interface HiveBadge {
  key: string;
  label: string;
  tone: "warn" | "muted" | "ok" | "honey" | "info";
}

export function hiveBadgesForState(state: HiveShareUiState | null | undefined): HiveBadge[] {
  if (!state) {
    return [];
  }
  const badges: HiveBadge[] = [];
  if (state.pull_only && state.has_file) {
    badges.push({ key: "pull", label: "Mode tirage", tone: "info" });
  } else if (state.shared) {
    badges.push({ key: "shared", label: "Partagé", tone: "honey" });
  } else if (state.preparing) {
    badges.push({ key: "prep", label: "Préparation au partage", tone: "muted" });
  } else if (!state.has_file) {
    badges.push({ key: "nodl", label: "Non partageable : à télécharger", tone: "muted" });
  }
  if (state.hive_keep) {
    badges.push({ key: "keep", label: "Gardé", tone: "ok" });
  }
  if (state.transfer_active) {
    const pct = state.transfer_progress_pct != null ? ` ${state.transfer_progress_pct} %` : "";
    badges.push({ key: "xfer", label: `En transfert${pct}`, tone: "honey" });
  }
  return badges;
}

export interface HiveMediaSummary {
  shared: boolean;
  transfer_active: boolean;
  preparing: boolean;
  pull_only: boolean;
}

export function hiveBadgesFromMediaSummary(summary: HiveMediaSummary | undefined): HiveBadge[] {
  if (!summary) {
    return [];
  }
  const pseudo: HiveShareUiState = {
    shareable: summary.shared,
    shared: summary.shared,
    exclude_all: false,
    excluded_ruche_ids: [],
    preparing: summary.preparing,
    transfer_active: summary.transfer_active,
    transfer_progress_pct: null,
    hive_keep: false,
    keep: false,
    pull_only: summary.pull_only,
    has_file: true,
    catalog_entry_local: false,
  };
  return hiveBadgesForState(pseudo);
}

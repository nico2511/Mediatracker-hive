import type { HiveShareUiState } from "./hiveBadges";
import { hiveShareFicheStatus } from "./hiveShareStatus";

export type HiveEpisodeShareChipTone = "honey" | "muted" | "danger" | "info" | "ok" | "warn";

function fingerprintTone(pct: number): HiveEpisodeShareChipTone {
  if (pct >= 67) {
    return "ok";
  }
  if (pct >= 34) {
    return "warn";
  }
  return "muted";
}

export interface HiveEpisodeShareChip {
  key: string;
  labelShort: string;
  labelLong: string;
  tone: HiveEpisodeShareChipTone;
  showBar?: boolean;
  barPct?: number | null;
}

function pctLabel(pct: number | null | undefined): string {
  if (pct == null || Number.isNaN(pct)) {
    return "";
  }
  return ` ${Math.round(pct)} %`;
}

function inboundLabels(pct: number | null | undefined): { short: string; long: string; barPct: number | null } {
  const suffix = pctLabel(pct);
  return {
    short: `Entrant${suffix}`,
    long: `Transfert entrant${suffix}`,
    barPct: pct != null ? Math.round(pct) : null,
  };
}

function outboundLabels(pct: number | null | undefined): { short: string; long: string; barPct: number | null } {
  const suffix = pctLabel(pct);
  return {
    short: `Envoi${suffix}`,
    long: `Transfert sortant${suffix}`,
    barPct: pct != null ? Math.round(pct) : null,
  };
}

/** Pastilles épisode / film (ordre d'affichage stable). */
export function hiveEpisodeShareChips(
  state: HiveShareUiState | null | undefined,
): HiveEpisodeShareChip[] {
  if (!state) {
    return [];
  }
  const chips: HiveEpisodeShareChip[] = [];

  if (state.transfer_active && !state.has_file) {
    const inbound = inboundLabels(state.transfer_progress_pct);
    chips.push({
      key: "inbound",
      labelShort: inbound.short.trim(),
      labelLong: inbound.long.trim(),
      tone: "info",
      showBar: inbound.barPct != null,
      barPct: inbound.barPct,
    });
    return chips;
  }

  if (!state.has_file) {
    return [];
  }

  const status = hiveShareFicheStatus(state, {});
  if (status.phase === "error") {
    return [{ key: "error", labelShort: "Erreur", labelLong: "Erreur", tone: "danger" }];
  }

  if (status.phase === "preparing") {
    const partial = state.fingerprint_partial_size ?? 0;
    const total = state.fingerprint_total_size ?? 0;
    if (total > 0) {
      const pct =
        partial >= total ? 99 : Math.min(99, Math.max(0, Math.round((100 * partial) / total)));
      const label = `Empreinte ${pct} %`;
      chips.push({
        key: "empreinte",
        labelShort: label,
        labelLong: label,
        tone: fingerprintTone(pct),
        showBar: true,
        barPct: pct,
      });
      return chips;
    }
    chips.push({ key: "prep", labelShort: "Préparation", labelLong: "Préparation", tone: "muted" });
    return chips;
  }

  const activelyShared = Boolean(state.shared && !state.exclude_all);

  if (activelyShared) {
    chips.push({ key: "shared", labelShort: "Partagé", labelLong: "Partagé", tone: "info" });
  } else if (state.received_via_hive) {
    chips.push({
      key: "received",
      labelShort: "Reçu via Hive",
      labelLong: "Reçu via Hive",
      tone: "ok",
    });
  } else if (status.phase === "not_shared" || status.phase === "intention") {
    chips.push({ key: "not-shared", labelShort: "Non partagé", labelLong: "Non partagé", tone: "muted" });
  }

  if (state.transfer_outbound_active && activelyShared) {
    const outbound = outboundLabels(state.transfer_outbound_progress_pct);
    chips.push({
      key: "outbound",
      labelShort: outbound.short.trim(),
      labelLong: outbound.long.trim(),
      tone: "honey",
      showBar: outbound.barPct != null,
      barPct: outbound.barPct,
    });
  }

  return chips;
}

/** Premier chip (rétrocompatibilité tests simples). */
export function hiveEpisodeShareChip(state: HiveShareUiState | null | undefined): HiveEpisodeShareChip | null {
  const chips = hiveEpisodeShareChips(state);
  return chips[0] ?? null;
}
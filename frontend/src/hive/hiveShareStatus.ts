import type { HiveShareUiState } from "./hiveBadges";

export type HiveShareFichePhase =
  | "hidden"
  | "pull_only"
  | "no_file"
  | "error"
  | "preparing"
  | "local_ready"
  | "intention"
  | "not_shared";

export interface HiveShareFicheStatus {
  phase: HiveShareFichePhase;
  title: string;
  hint: string;
}

/** Intention utilisateur : partage autorisé (sans équivalent « shared » côté API). */
export function hiveSharingIntent(state: HiveShareUiState | null | undefined): boolean {
  if (!state || state.pull_only) {
    return false;
  }
  return !state.exclude_all;
}

export function shouldPollHiveSharing(
  state: HiveShareUiState | null | undefined,
  opts: { pollExhausted: boolean },
): boolean {
  if (!state || opts.pollExhausted) {
    return false;
  }
  if (state.preparing) {
    return true;
  }
  const intent = hiveSharingIntent(state);
  if (!intent || !state.has_file) {
    return false;
  }
  return !state.catalog_entry_local;
}

export function hiveShareFicheStatus(
  state: HiveShareUiState | null | undefined,
  opts: {
    requestError?: string | null;
    pollExhausted?: boolean;
    pollBusy?: boolean;
    retryAfterAtMs?: number;
  },
): HiveShareFicheStatus {
  const retrySuffix = () => {
    const at = opts.retryAfterAtMs ?? 0;
    if (at <= Date.now()) {
      return "";
    }
    const sec = Math.ceil((at - Date.now()) / 1000);
    return ` Nouvel essai serveur dans ${sec} s.`;
  };
  if (!state) {
    if (opts.requestError) {
      return {
        phase: "error",
        title: "Erreur de requête",
        hint: opts.requestError + retrySuffix(),
      };
    }
    if (opts.pollBusy) {
      return {
        phase: "preparing",
        title: "Lecture de l’état",
        hint: "Chargement de l’état de partage Hive…",
      };
    }
    return { phase: "hidden", title: "", hint: "" };
  }
  if (state.pull_only && state.has_file) {
    return {
      phase: "pull_only",
      title: "Mode tirage",
      hint: "Cette instance ne publie pas : seuls les téléchargements depuis Hive sont possibles.",
    };
  }
  if (opts.requestError) {
    return {
      phase: "error",
      title: "Erreur de requête",
      hint: opts.requestError + retrySuffix(),
    };
  }
  if (!state.has_file) {
    return {
      phase: "no_file",
      title: "Non partageable : à télécharger",
      hint: "Aucun fichier local : le partage Hive reste impossible tant que le téléchargement n’a pas abouti.",
    };
  }
  if (state.preparing) {
    const partial = state.fingerprint_partial_size ?? 0;
    const total = state.fingerprint_total_size ?? 0;
    let hint = "Empreinte du fichier en cours.";
    if (total > 0) {
      const rawPct = (100 * partial) / total;
      const pct = partial >= total ? 99 : Math.min(99, Math.max(0, Math.round(rawPct)));
      hint = `Empreinte du fichier en cours : ${pct} % (${partial.toLocaleString("fr-FR")} / ${total.toLocaleString("fr-FR")} octets).`;
    }
    return {
      phase: "preparing",
      title: "Préparation au partage",
      hint,
    };
  }
  const intent = hiveSharingIntent(state);
  if (intent && state.catalog_entry_local && state.shareable) {
    return {
      phase: "local_ready",
      title: "Annonce locale prête",
      hint: "L’offre signée est enregistrée localement. La propagation chez les pairs n’est pas garantie ici.",
    };
  }
  if (intent) {
    return {
      phase: "intention",
      title: "Intention de partage enregistrée",
      hint: (opts.pollExhausted
        ? "La mise à jour automatique est en pause. Utilisez Actualiser pour relancer la vérification."
        : "En attente de l’annonce locale (empreinte ou synchronisation catalogue).") + retrySuffix(),
    };
  }
  return {
    phase: "not_shared",
    title: "Non partagé",
    hint: "Le partage est désactivé pour cette fiche.",
  };
}

export const HIVE_SHARE_POLL_INTERVAL_MS = 5000;
export const HIVE_SHARE_POLL_MAX_MS = 120_000;
export const HIVE_SHARE_POLL_MAX_ATTEMPTS = 24;

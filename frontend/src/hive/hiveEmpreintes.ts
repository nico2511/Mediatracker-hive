export interface EmpreinteRow {
  media_item_id: number;
  season: number | null;
  episode: number | null;
  slot_label: string;
  file_state: string;
  file_detail: string;
  chunk_state: string;
  chunk_detail: string;
  phase: string;
  chunk_progress_pct: number | null;
  metadata_inconsistent: boolean;
}

export interface EmpreintesPage {
  media_id: number;
  season: number | null;
  ready_count: number;
  pending_count: number;
  total: number;
  rows: EmpreinteRow[];
}

export interface SharingScope {
  media_id: number;
  season: number | null;
  file_count: number;
  shared_count: number;
  any_shared: boolean;
  can_act: boolean;
  pull_only: boolean;
  message: string | null;
}

const PHASE_LABELS: Record<string, string> = {
  pret: "Prêt",
  en_file: "En file",
  calcul: "Calcul",
  en_attente: "En attente",
  a_controler: "À contrôler",
  ecart: "Écart",
  erreur: "Erreur",
};

export function empreintePhaseLabel(phase: string): string {
  return PHASE_LABELS[phase] || phase;
}

export function empreintePhaseClass(phase: string): string {
  switch (phase) {
    case "pret":
      return "ok";
    case "ecart":
    case "erreur":
      return "danger";
    case "a_controler":
      return "info";
    default:
      return "warn";
  }
}

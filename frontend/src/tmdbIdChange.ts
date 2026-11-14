export interface TmdbIdEpisodeLine {
  item_id: number | null;
  season: number | null;
  episode: number | null;
  title: string | null;
  has_file?: boolean;
  file_paths?: string[];
  reason?: string;
}

export interface TmdbIdChangePreview {
  previous_tmdb_id: number;
  new_tmdb_id: number;
  orphans_if_keep: TmdbIdEpisodeLine[];
  removed_if_rebuild: TmdbIdEpisodeLine[];
  files_to_reassociate: TmdbIdEpisodeLine[];
}

export type TmdbIdConfirmChoice = "keep_episodes" | "rebuild_episodes";

function isEpisodeLineList(value: unknown): value is TmdbIdEpisodeLine[] {
  return Array.isArray(value);
}

export function readTmdbIdChangePreview(problem?: Record<string, unknown>): TmdbIdChangePreview | null {
  if (!problem?.needs_confirm) {
    return null;
  }
  const block = problem.tmdb_id_change;
  if (!block || typeof block !== "object") {
    return null;
  }
  const raw = block as Record<string, unknown>;
  if (typeof raw.previous_tmdb_id !== "number" || typeof raw.new_tmdb_id !== "number") {
    return null;
  }
  if (
    !isEpisodeLineList(raw.orphans_if_keep) ||
    !isEpisodeLineList(raw.removed_if_rebuild) ||
    !isEpisodeLineList(raw.files_to_reassociate)
  ) {
    return null;
  }
  return {
    previous_tmdb_id: raw.previous_tmdb_id,
    new_tmdb_id: raw.new_tmdb_id,
    orphans_if_keep: raw.orphans_if_keep,
    removed_if_rebuild: raw.removed_if_rebuild,
    files_to_reassociate: raw.files_to_reassociate,
  };
}

export function formatEpisodeSlot(line: TmdbIdEpisodeLine): string {
  if (line.season == null || line.episode == null) {
    return line.title?.trim() || "Épisode";
  }
  const label = `S${line.season}E${line.episode}`;
  return line.title ? `${label} — ${line.title}` : label;
}

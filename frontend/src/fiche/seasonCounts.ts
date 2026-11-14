/** Season header counters for the fiche (catalog vs on-disk vs sources). */

export type SeasonCountsEpisode = {
  watched: boolean;
  downloaded: boolean;
  file_status?: string;
  source_available: boolean;
  download_active?: boolean;
  download_busy?: boolean;
};

export type SeasonCountsGroup = {
  episode_count: number | null;
  file_status_counts?: { present: number; trash: number; absent: number; downloading: number };
  episodes: SeasonCountsEpisode[];
};

export function watchedCount(group: SeasonCountsGroup): number {
  return group.episodes.filter((row) => row.watched).length;
}

export function presentOnDiskCount(group: SeasonCountsGroup): number {
  if (group.file_status_counts) {
    return group.file_status_counts.present;
  }
  return group.episodes.filter((row) => row.file_status === "present" || row.downloaded).length;
}

/** Expected episodes in the catalogue (TMDB season size when known). */
export function catalogEpisodeCount(group: SeasonCountsGroup): number {
  if (group.episode_count != null && group.episode_count > 0) {
    return group.episode_count;
  }
  return group.episodes.length;
}

export function trackedSlotCount(group: SeasonCountsGroup): number {
  return group.episodes.length;
}

/** Catalogue slots with no MediaItem row yet. */
export function missingCatalogSlots(group: SeasonCountsGroup): number {
  if (group.episode_count == null || group.episode_count <= 0) {
    return 0;
  }
  return Math.max(0, group.episode_count - trackedSlotCount(group));
}

/** Source chosen, file not on disk yet (not the same as a missing local file). */
export function readyWithoutFileCount(group: SeasonCountsGroup): number {
  return group.episodes.filter((row) => {
    if (row.download_active || row.download_busy || row.file_status === "downloading") {
      return false;
    }
    return row.source_available && !row.downloaded && row.file_status !== "present";
  }).length;
}

export function absentWithoutSourceCount(group: SeasonCountsGroup): number {
  return group.episodes.filter((row) => {
    if (row.file_status === "present" || row.downloaded) {
      return false;
    }
    if (row.download_active || row.download_busy || row.file_status === "downloading") {
      return false;
    }
    if (row.source_available && !row.downloaded) {
      return false;
    }
    const status = row.file_status || "absent";
    return status === "absent";
  }).length;
}

export function missingCatalogLabel(group: SeasonCountsGroup): string {
  const n = missingCatalogSlots(group);
  return n === 1 ? "1 non suivi au catalogue" : `${n} non suivis au catalogue`;
}

export function readyWithoutFileLabel(group: SeasonCountsGroup): string {
  const n = readyWithoutFileCount(group);
  if (n <= 0) {
    return "";
  }
  return n === 1 ? "1 à télécharger" : `${n} à télécharger`;
}

export function absentWithoutSourceLabel(group: SeasonCountsGroup): string {
  const n = absentWithoutSourceCount(group);
  return n === 1 ? "1 sans fichier ni source" : `${n} sans fichier ni source`;
}

export function seasonProgressDetail(group: SeasonCountsGroup): string {
  const catalog = catalogEpisodeCount(group);
  const parts = [
    `vus ${watchedCount(group)}`,
    `sur disque ${presentOnDiskCount(group)}`,
    `au catalogue ${catalog}`,
  ];
  const missing = missingCatalogSlots(group);
  if (missing > 0) {
    parts.push(`non suivis ${missing}`);
  }
  const ready = readyWithoutFileCount(group);
  if (ready > 0) {
    parts.push(`sources dispo ${ready}`);
  }
  return parts.join(" / ");
}

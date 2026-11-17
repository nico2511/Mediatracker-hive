export const DOWNLOAD_ACTIVE_STATES = new Set([
  "pending",
  "resolving",
  "queued",
  "downloading",
  "post_processing",
]);

export interface MergeableDownloadJob {
  id: number;
  state: string;
}

function sortJobsByIdDesc<T extends MergeableDownloadJob>(jobs: T[]): T[] {
  return [...jobs].sort((a, b) => b.id - a.id);
}

/** Fusionne une trame SSE avec la liste déjà chargée (ne retire pas les jobs absents de la trame). */
export function mergeDownloadJobs<T extends MergeableDownloadJob>(existing: readonly T[], incoming: readonly T[]): T[] {
  if (incoming.length === 0) {
    return sortJobsByIdDesc([...existing]);
  }
  const byId = new Map(existing.map((job) => [job.id, job]));
  for (const job of incoming) {
    byId.set(job.id, job);
  }
  return sortJobsByIdDesc([...byId.values()]);
}

export function shouldResyncDownloadsFromSnapshot<T extends MergeableDownloadJob>(
  merged: readonly T[],
  incoming: readonly T[],
): boolean {
  const hasLocalActive = merged.some((job) => DOWNLOAD_ACTIVE_STATES.has(job.state));
  if (incoming.length === 0) {
    return hasLocalActive;
  }
  const incomingIds = new Set(incoming.map((job) => job.id));
  return merged.some((job) => DOWNLOAD_ACTIVE_STATES.has(job.state) && !incomingIds.has(job.id));
}

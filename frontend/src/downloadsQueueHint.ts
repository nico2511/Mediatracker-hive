import { formatParisClock } from "./time";

export interface DownloadQueueHintJob {
  id: number;
  state: string;
  error?: string | null;
  error_code?: string | null;
  next_retry_at?: string | null;
  source_rounds?: number;
}

const MAX_SOURCE_ROUNDS = 4;

/** Indication de position dans la file (téléchargements). */
export function downloadQueueHint(
  job: DownloadQueueHintJob,
  queuedIdsInOrder: number[],
  hasRunning: boolean,
): string {
  const index = queuedIdsInOrder.findIndex((id) => id === job.id);
  const position = index >= 0 ? index + 1 : 1;
  const prefix = `Position ${position}`;
  const rounds = job.source_rounds ?? 0;
  let roundHint = "";
  if (rounds >= MAX_SOURCE_ROUNDS && job.next_retry_at) {
    roundHint = ` · Prochaine recherche demain vers ${formatParisClock(job.next_retry_at)}`;
  } else if (rounds > 0) {
    const nextTour = Math.min(rounds + 1, MAX_SOURCE_ROUNDS);
    roundHint = ` · tour ${nextTour} sur ${MAX_SOURCE_ROUNDS}`;
  }
  if (job.state === "pending") {
    const message = (job.error || "").trim();
    if (message || job.error_code) {
      const detail = message || job.error_code || "";
      if (job.next_retry_at) {
        return `${prefix} · ${detail} · prochain essai ${formatParisClock(job.next_retry_at)}${roundHint}`;
      }
      return `${prefix} · ${detail}${roundHint}`;
    }
    return `${prefix} · En attente de débridage${roundHint}`;
  }
  if (hasRunning) {
    return `${prefix} · démarre après un job actif${roundHint}`;
  }
  return `${prefix} · En pause dans aria2${roundHint}`;
}
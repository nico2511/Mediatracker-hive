import { formatParisClock } from "./time";

export interface DownloadQueueHintJob {
  id: number;
  state: string;
  error?: string | null;
  error_code?: string | null;
  next_retry_at?: string | null;
}

/** Indication de position dans la file (téléchargements). */
export function downloadQueueHint(
  job: DownloadQueueHintJob,
  queuedIdsInOrder: number[],
  hasRunning: boolean,
): string {
  const index = queuedIdsInOrder.findIndex((id) => id === job.id);
  const position = index >= 0 ? index + 1 : 1;
  const prefix = `Position ${position}`;
  if (job.state === "pending") {
    const message = (job.error || "").trim();
    if (message || job.error_code) {
      const detail = message || job.error_code || "";
      if (job.next_retry_at) {
        return `${prefix} · ${detail} · prochain essai ${formatParisClock(job.next_retry_at)}`;
      }
      return `${prefix} · ${detail}`;
    }
    return `${prefix} · En attente de débridage`;
  }
  if (hasRunning) {
    return `${prefix} · démarre après un job actif`;
  }
  return `${prefix} · En pause dans aria2`;
}

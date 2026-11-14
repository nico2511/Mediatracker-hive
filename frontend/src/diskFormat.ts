export function formatBytes(size: number | null): string {
  if (size == null) {
    return "—";
  }
  const sign = size < 0 ? "−" : "";
  const abs = Math.abs(size);
  if (abs >= 1024 * 1024 * 1024) {
    return `${sign}${(abs / (1024 * 1024 * 1024)).toFixed(1)} Go`;
  }
  if (abs >= 1024 * 1024) {
    return `${sign}${(abs / (1024 * 1024)).toFixed(1)} Mo`;
  }
  if (abs >= 1024) {
    return `${sign}${(abs / 1024).toFixed(1)} Ko`;
  }
  return `${sign}${abs} o`;
}

export const jobLabels: Record<string, string> = {
  pending: "en attente",
  resolving: "débridage",
  queued: "en file",
  downloading: "en cours",
  post_processing: "rangement",
  done: "terminé",
  failed: "échec",
  cancelled: "annulé",
};

export function fileSizeLine(writtenBytes: number, reservedBytes: number): string {
  if (reservedBytes > writtenBytes) {
    return `${formatBytes(writtenBytes)} écrits · ${formatBytes(reservedBytes)} réservés`;
  }
  return formatBytes(writtenBytes);
}

export function folderSizeLine(writtenBytes: number, reservedBytes: number): string {
  return fileSizeLine(writtenBytes, reservedBytes);
}

export function jobLine(file: { orphan: boolean; job_id: number | null; job_state: string }): string {
  if (file.orphan) {
    return "orphelin";
  }
  if (file.job_id == null) {
    return "téléchargement en cours";
  }
  const state = jobLabels[file.job_state] || file.job_state || "job";
  return `job ${file.job_id} · ${state}`;
}

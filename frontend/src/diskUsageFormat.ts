import { fileSizeLine, folderSizeLine, formatBytes } from "./diskFormat";
import type { DiskFile, DiskFolder, DiskVolume } from "./useDiskUsage";
import { jobLine } from "./diskFormat";

export function whenComputed(value: string | null): string {
  if (!value) {
    return "pas encore calculé";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

export function libraryBytes(folders: DiskFolder[]): number {
  return folders.filter((row) => row.key === "movies" || row.key === "series").reduce((sum, row) => sum + row.bytes, 0);
}

export function folderLine(row: DiskFolder): string {
  if (row.missing) {
    return "dossier absent";
  }
  const reserved = row.reserved_bytes ?? 0;
  const written = row.written_bytes ?? row.bytes;
  return folderSizeLine(written, reserved);
}

export function incompleteFiles(folders: DiskFolder[]): DiskFile[] {
  return folders.find((row) => row.key === "incomplete")?.files ?? [];
}

export function incompleteFolder(folders: DiskFolder[]): DiskFolder | undefined {
  return folders.find((row) => row.key === "incomplete");
}

export function fileSize(file: DiskFile): string {
  return fileSizeLine(file.written_bytes, file.reserved_bytes);
}

export function gap(volume: DiskVolume): string {
  if (volume.other_bytes > 0) {
    return `${formatBytes(volume.other_bytes)} d'autres données sur ce volume.`;
  }
  if (volume.other_bytes < 0) {
    return "Les dossiers mesurés dépassent l'espace occupé (liens physiques ou autre comptage).";
  }
  return "Rien d'autre de mesuré sur ce volume.";
}

export function incompleteTrashEligible(folders: DiskFolder[]): number {
  const folder = incompleteFolder(folders);
  if (!folder) {
    return 0;
  }
  if (typeof folder.incomplete_trash_count === "number") {
    return folder.incomplete_trash_count;
  }
  return incompleteFiles(folders).filter((file) => file.orphan).length;
}

function formatKeepUntil(value: string | null | undefined): string {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
}

export function keptRecentLine(folders: DiskFolder[]): string {
  const folder = incompleteFolder(folders);
  if (!folder?.incomplete_kept_recent_count) {
    return "";
  }
  const count = folder.incomplete_kept_recent_count;
  const noun = count === 1 ? "fichier récent gardé" : "fichiers récents gardés";
  const until = formatKeepUntil(folder.incomplete_keep_until);
  return until ? `${count} ${noun} jusqu'au ${until}.` : `${count} ${noun}.`;
}

export { jobLine };
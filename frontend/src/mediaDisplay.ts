export interface MediaBadgeInput {
  on_hold?: boolean;
  watched?: boolean;
  availability?: string;
  disk_label?: string | null;
}

export interface MediaDiskInput {
  on_disk?: boolean;
  availability?: string;
}

export function mediaBadge(item: MediaBadgeInput): { name: string; label: string } {
  if (item.on_hold) {
    return { name: "pause", label: "En pause" };
  }
  if (item.watched) {
    return { name: "watched", label: "Vu" };
  }
  if (item.availability === "on_disk") {
    return { name: "disk", label: "Sur le disque" };
  }
  if (item.availability === "partial") {
    return { name: "disk", label: item.disk_label || "Partiel" };
  }
  if (item.availability === "available") {
    return { name: "downloaded", label: "Disponible" };
  }
  if (item.availability === "unavailable") {
    return { name: "waiting", label: "Pas encore disponible" };
  }
  return { name: "waiting", label: "En attente" };
}

export function mediaOnDisk(item: MediaDiskInput): boolean {
  if (item.on_disk === true) {
    return true;
  }
  if (item.on_disk === false) {
    return false;
  }
  return item.availability === "on_disk" || item.availability === "partial";
}

export function mediaWaiting(item: MediaBadgeInput): boolean {
  if (item.availability === "unavailable") {
    return false;
  }
  return item.availability === "waiting" && !item.watched;
}

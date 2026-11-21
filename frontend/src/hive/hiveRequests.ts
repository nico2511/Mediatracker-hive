import { mediaKind } from "../labels";

export type HiveMyRequestStatus = "pending" | "transfer" | "prompt" | "retracted";

export interface HiveMyRequestDownloadProgress {
  media_item_id: number | null;
  progress: number;
  done_bytes: number;
  size: number | null;
  state: string;
}

export interface HiveMyRequestItem {
  media_id: number;
  media_item_id: number;
  work_key: string;
  title: string;
  type: string;
  tmdb_id?: number | null;
  season: number | null;
  episode: number | null;
  status: HiveMyRequestStatus;
  hive_request_available: boolean;
  poster_url?: string | null;
  requested_at?: string | null;
}

export interface HiveMyRequestStatusCounts {
  pending: number;
  transfer: number;
  prompt: number;
  retracted: number;
}

export interface HiveMyRequestGroup {
  media_id: number;
  title: string;
  type: string;
  poster_url: string | null;
  episodes: HiveMyRequestItem[];
  statusCounts: HiveMyRequestStatusCounts;
  hasTransfer: boolean;
  sortAt: string;
}

export type HiveReceivedRequestStatus =
  | "pending"
  | "preparing"
  | "sharing"
  | "blocked";

export interface HiveReceivedRequestItem {
  request_id: string;
  work_key: string;
  title: string;
  type: string;
  season: number | null;
  episode: number | null;
  media_item_id?: number;
  already_shared?: boolean;
  status: HiveReceivedRequestStatus | string;
  requester_count?: number;
  requested_versions?: string[];
}

export function hiveReceivedRequestPending(row: Pick<HiveReceivedRequestItem, "status">): boolean {
  return row.status === "pending";
}

export function hiveReceivedFollowUpLine(
  row: Pick<HiveReceivedRequestItem, "status">,
  options: { compact?: boolean } = {},
): string | null {
  const compact = Boolean(options.compact);
  switch (row.status) {
    case "preparing":
      return "Empreinte du fichier en cours.";
    case "sharing":
      return compact ? "Partage en cours." : "Partage en cours : synchronisation du catalogue local.";
    case "blocked":
      return compact
        ? "Adresse publique Hive manquante."
        : "Adresse publique Hive manquante : l’offre ne peut pas encore être publiée.";
    default:
      return null;
  }
}

export function hiveReceivedAcceptNote(
  row: Pick<HiveReceivedRequestItem, "status"> | null | undefined,
  options: { compact?: boolean } = {},
): string {
  const followUp = row ? hiveReceivedFollowUpLine(row, options) : null;
  if (followUp) {
    return followUp;
  }
  return "Demande acceptée.";
}

export function hiveReceivedAcceptNoteAfterReload(
  requestId: string,
  items: readonly Pick<HiveReceivedRequestItem, "request_id" | "status">[],
  options: { compact?: boolean } = {},
): string {
  const updated = items.find((item) => item.request_id === requestId);
  return hiveReceivedAcceptNote(updated, options);
}

export function hiveReceivedAcceptNotePair(
  requestId: string,
  items: readonly Pick<HiveReceivedRequestItem, "request_id" | "status">[],
): { text: string; compact: string } {
  return {
    text: hiveReceivedAcceptNoteAfterReload(requestId, items),
    compact: hiveReceivedAcceptNoteAfterReload(requestId, items, { compact: true }),
  };
}

export interface HiveWantedRequestItem {
  work_key: string;
  title: string;
  year?: number | null;
  type: string;
  tmdb_id?: number | null;
  season: number | null;
  episode: number | null;
  poster_url?: string | null;
  requester_count: number;
  volunteering: boolean;
}

export function hiveWantedStatusLine(requesterCount: number): string {
  const demandeurs =
    requesterCount === 1 ? "1 demandeur anonyme" : `${requesterCount} demandeurs anonymes`;
  return demandeurs;
}

export function hiveWantedTakeLabel(volunteering: boolean): string {
  if (volunteering) {
    return "Ne plus m'en occuper";
  }
  return "Je m'en occupe";
}

export function hiveRequestEpisodeLabel(season: number | null, episode: number | null): string {
  if (season == null || episode == null) {
    return "";
  }
  return `S${season} · E${String(episode).padStart(2, "0")}`;
}

export function hiveRequestTitleLine(item: Pick<HiveMyRequestItem, "title" | "type" | "season" | "episode">): string {
  const ep = hiveRequestEpisodeLabel(item.season, item.episode);
  if (ep) {
    return `${item.title} · ${ep}`;
  }
  return item.title;
}

export function hiveRequestKindLine(item: Pick<HiveMyRequestItem, "type" | "status">): string {
  const kind = mediaKind(item.type);
  if (item.status === "retracted") {
    return `${kind} · suivi en attente · retrait mémorisé`;
  }
  if (item.status === "prompt") {
    return `${kind} · suivi en attente`;
  }
  if (item.status === "transfer") {
    return `${kind} · suivi · un possesseur a accepté`;
  }
  return `${kind} · suivi en attente`;
}

export function hiveRequestMineBadge(status: HiveMyRequestStatus): string | null {
  if (status === "prompt" || status === "retracted") {
    return null;
  }
  return "Demandé à la ruche";
}

export function hiveRequestMineSubBadge(status: HiveMyRequestStatus): string {
  if (status === "retracted") {
    return "Non demandé à la ruche";
  }
  if (status === "transfer") {
    return "En cours de transfert";
  }
  if (status === "prompt") {
    return "Recherche sans résultat";
  }
  return "En attente d'un possesseur";
}

function emptyStatusCounts(): HiveMyRequestStatusCounts {
  return { pending: 0, transfer: 0, prompt: 0, retracted: 0 };
}

function episodeSortKey(item: HiveMyRequestItem): number {
  const season = item.season ?? 0;
  const episode = item.episode ?? 0;
  return season * 10_000 + episode;
}

function groupSortTimestamp(items: HiveMyRequestItem[]): string {
  let best = "";
  for (const row of items) {
    const at = row.requested_at ?? "";
    if (at > best) {
      best = at;
    }
  }
  return best;
}

export function hiveMyRequestAggregateLine(counts: HiveMyRequestStatusCounts): string {
  const parts: string[] = [];
  if (counts.pending > 0) {
    parts.push(`${counts.pending} en attente`);
  }
  if (counts.transfer > 0) {
    parts.push(`${counts.transfer} en transfert`);
  }
  if (counts.prompt > 0) {
    parts.push(`${counts.prompt} recherche sans résultat`);
  }
  if (counts.retracted > 0) {
    parts.push(`${counts.retracted} retrait mémorisé`);
  }
  return parts.join(" · ");
}

export function hiveMyRequestSeriesSubtitle(episodeCount: number): string {
  const n = episodeCount === 1 ? "1 épisode demandé" : `${episodeCount} épisodes demandés`;
  return n;
}

/** Ligne DEMANDE agrégée sur la fiche série (n demandés · m en transfert). */
export function hiveFicheSeriesDemandLine(items: HiveMyRequestItem[]): string {
  if (items.length === 0) {
    return "";
  }
  const transfer = items.filter((row) => row.status === "transfer").length;
  return `${hiveMyRequestSeriesSubtitle(items.length)} · ${transfer} en transfert`;
}

export function hiveMediaRequestsActiveAnnounced(items: HiveMyRequestItem[]): HiveMyRequestItem[] {
  return items.filter((row) => row.status === "pending" || row.status === "transfer");
}

export function groupHiveMyRequests(items: HiveMyRequestItem[]): HiveMyRequestGroup[] {
  const byMedia = new Map<number, HiveMyRequestItem[]>();
  for (const row of items) {
    const bucket = byMedia.get(row.media_id);
    if (bucket) {
      bucket.push(row);
    } else {
      byMedia.set(row.media_id, [row]);
    }
  }
  const groups: HiveMyRequestGroup[] = [];
  for (const [mediaId, episodes] of byMedia) {
    const sortedEpisodes = [...episodes].sort((a, b) => episodeSortKey(a) - episodeSortKey(b));
    const head = sortedEpisodes[0];
    const statusCounts = emptyStatusCounts();
    for (const ep of sortedEpisodes) {
      statusCounts[ep.status] += 1;
    }
    groups.push({
      media_id: mediaId,
      title: head.title,
      type: head.type,
      poster_url: head.poster_url ?? null,
      episodes: sortedEpisodes,
      statusCounts,
      hasTransfer: statusCounts.transfer > 0,
      sortAt: groupSortTimestamp(sortedEpisodes),
    });
  }
  return sortHiveMyRequestGroups(groups);
}

export function sortHiveMyRequestGroups(groups: HiveMyRequestGroup[]): HiveMyRequestGroup[] {
  return [...groups].sort((a, b) => {
    if (a.hasTransfer !== b.hasTransfer) {
      return a.hasTransfer ? -1 : 1;
    }
    const at = b.sortAt.localeCompare(a.sortAt);
    if (at !== 0) {
      return at;
    }
    return a.title.localeCompare(b.title, "fr");
  });
}

export function defaultExpandedMyRequestGroups(groups: HiveMyRequestGroup[]): Record<number, boolean> {
  const out: Record<number, boolean> = {};
  groups.forEach((group, index) => {
    out[group.media_id] = index === 0 || group.hasTransfer;
  });
  return out;
}

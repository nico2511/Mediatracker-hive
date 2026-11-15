import { mediaKind } from "../labels";

export type HiveMyRequestStatus = "pending" | "transfer" | "prompt" | "retracted";

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
}

export interface HiveReceivedRequestItem {
  request_id: string;
  work_key: string;
  title: string;
  type: string;
  season: number | null;
  episode: number | null;
  media_item_id?: number;
  already_shared?: boolean;
  status: string;
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
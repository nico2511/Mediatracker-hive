import { ApiError, api } from "../api";
import { alternateFollowType, type FollowMediaType } from "./parseTmdbInput";
import type { QueueItem } from "./offlineQueue";
import { removeQueueItem, updateQueueItemType } from "./offlineQueue";

export type SyncOutcome = "added" | "already" | "not_found";

export interface SyncResultRow {
  item: QueueItem;
  outcome: SyncOutcome;
  title: string | null;
  mediaId: number | null;
  triedAlternate: boolean;
  detail: string;
}

interface MediaOut {
  id: number;
  title: string;
}

async function followOnce(tmdbId: number, type: FollowMediaType): Promise<
  | { ok: true; created: MediaOut }
  | { ok: false; already: true; title: string | null }
  | { ok: false; notFound: true }
  | { ok: false; error: string }
> {
  try {
    const created = await api<MediaOut>("/api/v1/media", {
      method: "POST",
      body: JSON.stringify({ tmdb_id: tmdbId, type }),
    });
    return { ok: true, created };
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.status === 409 && /déjà suivi/i.test(err.message)) {
        return { ok: false, already: true, title: null };
      }
      if (err.status === 404 || (err.status === 422 && /tmdb/i.test(err.message))) {
        return { ok: false, notFound: true };
      }
      return { ok: false, error: err.message };
    }
    return { ok: false, error: "Serveur injoignable." };
  }
}

export async function syncQueueItem(item: QueueItem): Promise<SyncResultRow> {
  let triedAlternate = false;
  let current = item;
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const result = await followOnce(current.tmdb_id, current.type);
    if (result.ok) {
      await removeQueueItem(current.id);
      return {
        item: current,
        outcome: "added",
        title: result.created.title,
        mediaId: result.created.id,
        triedAlternate,
        detail: "Ajouté",
      };
    }
    if ("already" in result && result.already) {
      await removeQueueItem(current.id);
      return {
        item: current,
        outcome: "already",
        title: result.title,
        mediaId: null,
        triedAlternate,
        detail: "Déjà suivi",
      };
    }
    if ("notFound" in result && result.notFound && attempt === 0) {
      triedAlternate = true;
      const alt = alternateFollowType(current.type);
      const updated = await updateQueueItemType(current.id, alt);
      if (updated) {
        current = updated;
        continue;
      }
    }
    if ("error" in result && result.error && !("notFound" in result)) {
      return {
        item: current,
        outcome: "not_found",
        title: null,
        mediaId: null,
        triedAlternate,
        detail: result.error,
      };
    }
    break;
  }
  return {
    item: current,
    outcome: "not_found",
    title: null,
    mediaId: null,
    triedAlternate,
    detail: "ID introuvable",
  };
}

export async function syncAllQueueItems(items: QueueItem[]): Promise<SyncResultRow[]> {
  const rows: SyncResultRow[] = [];
  for (const item of items) {
    rows.push(await syncQueueItem(item));
  }
  return rows;
}

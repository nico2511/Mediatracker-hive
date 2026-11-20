import { requestAppSettingsCachesIfEmpty } from "../appSettingsCaches";
import { offlineQueueRetentionMs } from "../fixedRetention";
import type { FollowMediaType } from "./parseTmdbInput";

export interface QueueItem {
  id: string;
  tmdb_id: number;
  type: FollowMediaType;
  added_at: string;
}

export type AddQueueResult =
  | { ok: true; item: QueueItem }
  | { ok: false; reason: "duplicate"; existing: QueueItem }
  | { ok: false; reason: "invalid" };

export const FOLLOW_QUEUE_DB_NAME = "mt-follow-queue";
const DB_NAME = FOLLOW_QUEUE_DB_NAME;
const DB_VERSION = 1;
const STORE = "follow_queue";

function newId(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `q-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB indisponible."));
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id" });
        store.createIndex("by_tmdb_type", ["tmdb_id", "type"], { unique: true });
        store.createIndex("by_added", "added_at");
      }
    };
    request.onsuccess = () => resolve(request.result);
  });
}

function requestResult<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function withStore<T>(mode: IDBTransactionMode, run: (store: IDBObjectStore) => Promise<T>): Promise<T> {
  return openDb().then(
    (db) =>
      new Promise<T>((resolve, reject) => {
        const tx = db.transaction(STORE, mode);
        const store = tx.objectStore(STORE);
        let output: T;
        run(store)
          .then((value) => {
            output = value;
          })
          .catch(reject);
        tx.oncomplete = () => {
          db.close();
          resolve(output);
        };
        tx.onerror = () => reject(tx.error ?? new Error("Transaction IndexedDB échouée."));
      }),
  );
}

export function queueKey(tmdbId: number, type: FollowMediaType): string {
  return `${tmdbId}:${type}`;
}

export function isExpired(item: QueueItem, now = Date.now()): boolean {
  requestAppSettingsCachesIfEmpty();
  const added = Date.parse(item.added_at);
  if (Number.isNaN(added)) {
    return true;
  }
  const retention = offlineQueueRetentionMs();
  if (retention === null) {
    return false;
  }
  return now - added > retention;
}

export async function purgeExpiredQueue(now = Date.now()): Promise<QueueItem[]> {
  const all = await withStore("readonly", (store) => requestResult(store.getAll()) as Promise<QueueItem[]>);
  const removed = all.filter((row) => isExpired(row, now));
  if (!removed.length) {
    return [];
  }
  await withStore("readwrite", async (store) => {
    for (const row of removed) {
      await requestResult(store.delete(row.id));
    }
  });
  return removed;
}

export async function listQueueItems(): Promise<QueueItem[]> {
  await purgeExpiredQueue();
  const rows = await withStore("readonly", (store) => requestResult(store.getAll()) as Promise<QueueItem[]>);
  return rows.sort((a, b) => b.added_at.localeCompare(a.added_at));
}

export async function findQueueDuplicate(
  tmdbId: number,
  type: FollowMediaType,
): Promise<QueueItem | null> {
  return withStore("readonly", (store) => {
    const index = store.index("by_tmdb_type");
    return requestResult(index.get([tmdbId, type])) as Promise<QueueItem | undefined>;
  }).then((row) => row ?? null);
}

export async function addQueueItem(
  tmdbId: number,
  type: FollowMediaType,
  addedAt = new Date().toISOString(),
): Promise<AddQueueResult> {
  if (!Number.isFinite(tmdbId) || tmdbId < 1) {
    return { ok: false, reason: "invalid" };
  }
  const dup = await findQueueDuplicate(tmdbId, type);
  if (dup) {
    return { ok: false, reason: "duplicate", existing: dup };
  }
  const item: QueueItem = { id: newId(), tmdb_id: tmdbId, type, added_at: addedAt };
  await withStore("readwrite", (store) => requestResult(store.add(item)));
  return { ok: true, item };
}

export async function removeQueueItem(id: string): Promise<void> {
  await withStore("readwrite", (store) => requestResult(store.delete(id)));
}

export async function clearFollowQueue(): Promise<void> {
  if (typeof indexedDB === "undefined") {
    return;
  }
  await new Promise<void>((resolve, reject) => {
    const request = indexedDB.deleteDatabase(DB_NAME);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error ?? new Error("Suppression IndexedDB impossible."));
    request.onblocked = () => resolve();
  });
}

export async function updateQueueItemType(id: string, type: FollowMediaType): Promise<QueueItem | null> {
  const existing = await withStore("readonly", (store) =>
    requestResult(store.get(id)) as Promise<QueueItem | undefined>,
  );
  if (!existing) {
    return null;
  }
  const dup = await findQueueDuplicate(existing.tmdb_id, type);
  if (dup && dup.id !== id) {
    return null;
  }
  const next: QueueItem = { ...existing, type };
  await withStore("readwrite", (store) => requestResult(store.put(next)));
  return next;
}

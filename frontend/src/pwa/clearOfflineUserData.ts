import { clearFollowQueue } from "./offlineQueue";

export const OFFLINE_STORAGE_PREFIX = "mt.offline.";

export function clearOfflineLocalStorage(): void {
  if (typeof localStorage === "undefined") {
    return;
  }
  const keys: string[] = [];
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index);
    if (key?.startsWith(OFFLINE_STORAGE_PREFIX)) {
      keys.push(key);
    }
  }
  for (const key of keys) {
    localStorage.removeItem(key);
  }
}

/** Données hors ligne propres à la session (snapshots + file de suivi). */
export async function clearOfflineUserData(): Promise<void> {
  clearOfflineLocalStorage();
  await clearFollowQueue();
}

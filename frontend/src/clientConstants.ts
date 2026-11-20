import { shallowRef } from "vue";

import { fetchSettings } from "./settings";

export interface ClientConstants {
  hive_join_wait_hours: number;
  hive_media_sharing_summary_id_limit: number;
}

const cache = shallowRef<ClientConstants | null>(null);
let loadInflight: Promise<ClientConstants | null> | null = null;

export async function ensureClientConstants(): Promise<ClientConstants | null> {
  if (cache.value) {
    return cache.value;
  }
  if (!loadInflight) {
    loadInflight = (async () => {
      try {
        const page = await fetchSettings();
        if (!page.client_constants) {
          return null;
        }
        cache.value = page.client_constants;
        return cache.value;
      } catch {
        return null;
      }
    })().finally(() => {
      loadInflight = null;
    });
  }
  return loadInflight;
}

export function clientConstantsSnapshot(): ClientConstants | null {
  return cache.value;
}

export function setClientConstantsForTests(row: ClientConstants | null): void {
  cache.value = row;
}

/** Délai d’attente adhésion Hive en ms, ou `null` si non chargé (ne pas expirer). */
export function hiveJoinWaitTtlMs(): number | null {
  const hours = clientConstantsSnapshot()?.hive_join_wait_hours;
  if (typeof hours !== "number") {
    return null;
  }
  return hours * 60 * 60 * 1000;
}

/** Limite d’ids pour le batch Hive, ou `null` = pas de troncature côté front. */
export function hiveMediaSummaryLimit(): number | null {
  const limit = clientConstantsSnapshot()?.hive_media_sharing_summary_id_limit;
  if (typeof limit !== "number") {
    return null;
  }
  return limit;
}

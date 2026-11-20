import { shallowRef } from "vue";

import { api } from "./api";

export interface FixedRetentionDays {
  event_journal_days: number;
  source_state_days: number;
  metrics_history_days: number;
  incomplete_orphan_keep_days: number;
}

const cache = shallowRef<FixedRetentionDays | null>(null);
let loadInflight: Promise<FixedRetentionDays | null> | null = null;

export async function loadFixedRetention(): Promise<FixedRetentionDays | null> {
  if (cache.value) {
    return cache.value;
  }
  if (!loadInflight) {
    loadInflight = (async () => {
      try {
        cache.value = await api<FixedRetentionDays>("/api/v1/settings/fixed-retention");
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

export function fixedRetentionSnapshot(): FixedRetentionDays | null {
  return cache.value;
}

export function setFixedRetentionForTests(row: FixedRetentionDays | null): void {
  cache.value = row;
}

/** Durée de conservation file hors-ligne en ms, ou `null` si non chargé (ne pas expirer). */
export function offlineQueueRetentionMs(): number | null {
  const days = fixedRetentionSnapshot()?.source_state_days;
  if (typeof days !== "number") {
    return null;
  }
  return days * 24 * 60 * 60 * 1000;
}

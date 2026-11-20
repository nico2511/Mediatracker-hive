import { clientConstantsSnapshot, ensureClientConstants } from "./clientConstants";
import { fixedRetentionSnapshot, loadFixedRetention } from "./fixedRetention";

let inflight: Promise<void> | null = null;

/** Charge client_constants et fixed-retention ; les erreurs sont ignorées. */
export function prefetchAppSettingsCaches(): Promise<void> {
  if (inflight) {
    return inflight;
  }
  inflight = Promise.all([ensureClientConstants(), loadFixedRetention()])
    .then(() => {})
    .catch(() => {})
    .finally(() => {
      inflight = null;
    });
  return inflight;
}

/** Relance le préchargement si au moins un cache est encore vide. */
export function requestAppSettingsCachesIfEmpty(): void {
  if (clientConstantsSnapshot() && fixedRetentionSnapshot()) {
    return;
  }
  void prefetchAppSettingsCaches();
}

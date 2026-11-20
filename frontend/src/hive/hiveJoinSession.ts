/** État d’adhésion en cours (invité), persistant au rechargement — sans lien d’invitation. */

import { requestAppSettingsCachesIfEmpty } from "../appSettingsCaches";
import { hiveJoinWaitTtlMs } from "../clientConstants";

export interface HiveJoinWaitState {
  ruche_id_hex: string;
  creator_id: string;
  creator_base_url: string;
  control_code: string;
}

const STORAGE_KEY = "mediatracker:hive-join-wait";
const STORAGE_VERSION = 2;

interface PersistedHiveJoinWait extends HiveJoinWaitState {
  v: number;
  started_at: string;
}

function isComplete(state: HiveJoinWaitState | null | undefined): state is HiveJoinWaitState {
  return Boolean(
    state?.ruche_id_hex && state.creator_id && state.creator_base_url && state.control_code,
  );
}

function purgeStale(raw: string | null): void {
  if (typeof localStorage === "undefined") {
    return;
  }
  if (!raw) {
    return;
  }
  localStorage.removeItem(STORAGE_KEY);
}

export function readHiveJoinWait(): HiveJoinWaitState | null {
  requestAppSettingsCachesIfEmpty();
  if (typeof localStorage === "undefined") {
    return null;
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (parsed.v !== STORAGE_VERSION) {
      purgeStale(raw);
      return null;
    }
    if ("link" in parsed) {
      purgeStale(raw);
      return null;
    }
    const started = typeof parsed.started_at === "string" ? Date.parse(parsed.started_at) : Number.NaN;
    const ttl = hiveJoinWaitTtlMs();
    if (!Number.isFinite(started)) {
      purgeStale(raw);
      return null;
    }
    if (ttl !== null && Date.now() - started > ttl) {
      purgeStale(raw);
      return null;
    }
    const state: HiveJoinWaitState = {
      ruche_id_hex: String(parsed.ruche_id_hex || ""),
      creator_id: String(parsed.creator_id || ""),
      creator_base_url: String(parsed.creator_base_url || ""),
      control_code: String(parsed.control_code || ""),
    };
    if (!isComplete(state)) {
      purgeStale(raw);
      return null;
    }
    return state;
  } catch {
    purgeStale(localStorage.getItem(STORAGE_KEY));
    return null;
  }
}

export function writeHiveJoinWait(state: HiveJoinWaitState): void {
  if (typeof localStorage === "undefined" || !isComplete(state)) {
    return;
  }
  const payload: PersistedHiveJoinWait = {
    v: STORAGE_VERSION,
    started_at: new Date().toISOString(),
    ...state,
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
}

export function clearHiveJoinWait(): void {
  if (typeof localStorage === "undefined") {
    return;
  }
  localStorage.removeItem(STORAGE_KEY);
}

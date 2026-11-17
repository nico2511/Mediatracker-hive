import { onMounted, ref, shallowRef, type Ref } from "vue";

import { api } from "../api";
import { fetchSettings, type SettingsPage } from "../settings";

type SourcesStatusPayload = {
  sources?: { source: string; available?: boolean }[] | null;
  download_ready?: boolean;
};

const adConnected: Ref<boolean> = ref(false);
const downloadReady: Ref<boolean> = ref(true);
const sourceAvailable = shallowRef<Record<string, boolean>>({});
const jellyfinConfigured: Ref<boolean> = ref(false);
const hiveEnabled: Ref<boolean> = ref(false);
const watchtowerConfigured: Ref<boolean> = ref(false);
const tmdbSet: Ref<boolean> = ref(false);
const serviceStatusLoaded: Ref<boolean> = ref(false);

let loadGeneration = 0;
let inflight: Promise<void> | null = null;

function applySettings(page: SettingsPage) {
  const byKey = new Map(page.settings.map((row) => [row.key, row]));
  const jellyUrl = String(byKey.get("jellyfin_url")?.value ?? "").trim();
  const jellyKeySet = byKey.get("jellyfin_api_key")?.set ?? false;
  jellyfinConfigured.value = Boolean(jellyUrl && jellyKeySet);

  const hiveRow = byKey.get("hive_enabled");
  hiveEnabled.value = hiveRow?.value === true || hiveRow?.value === "true";

  tmdbSet.value = byKey.get("tmdb_read_token")?.set ?? false;
}

function applySourcesStatus(body: SourcesStatusPayload) {
  downloadReady.value = body.download_ready !== false;
  const next: Record<string, boolean> = {};
  for (const row of body.sources ?? []) {
    if (!row?.source) {
      continue;
    }
    next[row.source] = row.available !== false;
  }
  sourceAvailable.value = next;
}

/** Recharge l’état partagé (connexion AllDebrid, sources, réglages). */
export async function refreshServiceStatus(): Promise<void> {
  const gen = ++loadGeneration;
  try {
    const [account, sourcesStatus, settingsPage, updateStatus] = await Promise.all([
      api<{ connected: boolean }>("/api/v1/integrations/alldebrid/account"),
      api<SourcesStatusPayload>("/api/v1/settings/sources/status"),
      fetchSettings(),
      api<{ watchtower_configured?: boolean }>("/api/v1/ha/update"),
    ]);
    if (gen !== loadGeneration) {
      return;
    }
    adConnected.value = Boolean(account.connected);
    applySourcesStatus(sourcesStatus);
    applySettings(settingsPage);
    watchtowerConfigured.value = Boolean(updateStatus.watchtower_configured);
    serviceStatusLoaded.value = true;
  } catch {
    if (gen === loadGeneration) {
      /* Conserve la dernière valeur connue. */
    }
  }
}

async function ensureServiceStatusLoaded(): Promise<void> {
  if (inflight) {
    await inflight;
    return;
  }
  inflight = refreshServiceStatus().finally(() => {
    inflight = null;
  });
  await inflight;
}

export function useServiceStatus() {
  onMounted(() => {
    void ensureServiceStatusLoaded();
  });

  function isSourceAvailable(id: string): boolean {
    return sourceAvailable.value[id] !== false;
  }

  return {
    adConnected,
    downloadReady,
    sourceAvailable,
    jellyfinConfigured,
    hiveEnabled,
    watchtowerConfigured,
    tmdbSet,
    serviceStatusLoaded,
    refresh: refreshServiceStatus,
    isSourceAvailable,
  };
}

/** Remet l’état partagé à zéro (tests Vitest). */
export function resetServiceStatusForTests(): void {
  adConnected.value = false;
  downloadReady.value = true;
  sourceAvailable.value = {};
  jellyfinConfigured.value = false;
  hiveEnabled.value = false;
  watchtowerConfigured.value = false;
  tmdbSet.value = false;
  serviceStatusLoaded.value = false;
  loadGeneration += 1;
  inflight = null;
}

/** Accès direct aux refs (tests). */
export function serviceStatusRefs() {
  return {
    adConnected,
    downloadReady,
    sourceAvailable,
    jellyfinConfigured,
    hiveEnabled,
    watchtowerConfigured,
    tmdbSet,
    serviceStatusLoaded,
  };
}
import { computed, ref, shallowRef } from "vue";

import { listQueueItems } from "./offlineQueue";
import { syncAllQueueItems, type SyncResultRow } from "./offlineSync";

export const SERVER_PROBE_MS = 3000;

export type ConnectivityMode = "online" | "offline" | "server_down";

const online = ref(typeof navigator !== "undefined" ? navigator.onLine : true);
const serverReachable = ref(true);
const probing = ref(false);
const lastProbeAt = ref<string | null>(null);
const queueCount = ref(0);
const syncResults = shallowRef<SyncResultRow[] | null>(null);
const backOnlineVisible = ref(false);

let probeTimer = 0;
let backOnlineTimer = 0;
let initialized = false;

export async function refreshQueueCount(): Promise<void> {
  try {
    const rows = await listQueueItems();
    queueCount.value = rows.length;
  } catch {
    queueCount.value = 0;
  }
}

export async function probeServer(timeoutMs = SERVER_PROBE_MS): Promise<boolean> {
  if (!navigator.onLine) {
    serverReachable.value = false;
    lastProbeAt.value = new Date().toISOString();
    return false;
  }
  probing.value = true;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch("/healthz", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    serverReachable.value = response.ok;
  } catch {
    serverReachable.value = false;
  } finally {
    window.clearTimeout(timer);
    probing.value = false;
    lastProbeAt.value = new Date().toISOString();
  }
  return serverReachable.value;
}

export async function tryFlushQueue(): Promise<void> {
  if (!navigator.onLine || !serverReachable.value) {
    return;
  }
  const pending = await listQueueItems();
  if (!pending.length) {
    return;
  }
  const results = await syncAllQueueItems(pending);
  syncResults.value = results;
  await refreshQueueCount();
  if (results.some((row) => row.outcome === "added" || row.outcome === "already")) {
    backOnlineVisible.value = true;
    window.clearTimeout(backOnlineTimer);
    backOnlineTimer = window.setTimeout(() => {
      if (queueCount.value === 0) {
        backOnlineVisible.value = false;
      }
    }, 10000);
  }
}

function scheduleProbe(): void {
  window.clearTimeout(probeTimer);
  probeTimer = window.setTimeout(() => {
    void probeServer().then((ok) => {
      if (ok) {
        void tryFlushQueue();
      }
    });
  }, 50);
}

export function initConnectivity(): void {
  if (initialized || typeof window === "undefined") {
    return;
  }
  initialized = true;
  const onOnline = () => {
    online.value = true;
    scheduleProbe();
  };
  const onOffline = () => {
    online.value = false;
    serverReachable.value = false;
  };
  window.addEventListener("online", onOnline);
  window.addEventListener("offline", onOffline);
  void refreshQueueCount();
  scheduleProbe();
  void tryFlushQueue();
}

export function useConnectivity() {
  const mode = computed<ConnectivityMode>(() => {
    if (!online.value) {
      return "offline";
    }
    if (!serverReachable.value) {
      return "server_down";
    }
    return "online";
  });

  const offlineShell = computed(() => mode.value === "offline");
  const serverActionsDisabled = computed(() => mode.value !== "online");

  async function retryConnection(): Promise<void> {
    const ok = await probeServer();
    if (ok) {
      await tryFlushQueue();
    }
  }

  function hideBackOnlineBanner(): void {
    backOnlineVisible.value = false;
  }

  function clearSyncResults(): void {
    syncResults.value = null;
  }

  return {
    mode,
    online,
    serverReachable,
    probing,
    lastProbeAt,
    queueCount,
    refreshQueueCount,
    syncResults,
    backOnlineVisible,
    offlineShell,
    serverActionsDisabled,
    probeServer,
    retryConnection,
    tryFlushQueue,
    hideBackOnlineBanner,
    clearSyncResults,
  };
}

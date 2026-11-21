<script setup lang="ts">
import { computed, onMounted, onUnmounted, provide, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import HiveGossipSyncBar from "../../components/hive/HiveGossipSyncBar.vue";
import { api } from "../../api";
import type { HiveMyRequestItem } from "../../hive/hiveRequests";
import type { HiveReceivedRequestItem } from "../../hive/hiveRequests";
import type { HiveWantedRequestItem } from "../../hive/hiveRequests";
import { hiveShellContextKey } from "../../hive/hiveShellContext";
import { parseIsoUtc } from "../../hive/hiveGossipSync";

type HiveTabView = {
  reload?: () => Promise<void> | void;
};

type HiveOverview = {
  enabled: boolean;
  next_gossip_at?: string | null;
  last_gossip_at?: string | null;
};

const route = useRoute();
const mineCount = ref<number | null>(null);
const receivedCount = ref<number | null>(null);
const wantedCount = ref<number | null>(null);
const hiveEnabled = ref(false);
const nextGossipMs = ref<number | null>(null);
const lastGossipMs = ref<number | null>(null);
const nowMs = ref(Date.now());
const activeViewRef = ref<HiveTabView | null>(null);
const gossipReloading = ref(false);

const HIVE_GOSSIP_RETRY_MS = 60_000;

let tickTimer: ReturnType<typeof setInterval> | undefined;
let retryTimer: ReturnType<typeof setTimeout> | undefined;
let dueHandledForMs: number | null = null;
let retryUsedForMs: number | null = null;
let alive = true;

const tabs = [
  { id: "catalogue", label: "Catalogue", to: "/hive/catalogue" },
  { id: "demandes", label: "Mes demandes", to: "/hive/demandes" },
  { id: "demandes-autres", label: "Demandes des autres", to: "/hive/demandes-autres" },
] as const;

const activeTab = computed(() => {
  if (
    route.path.startsWith("/hive/demandes-autres") ||
    route.path.startsWith("/hive/recues") ||
    route.path.startsWith("/hive/recherche")
  ) {
    return "demandes-autres";
  }
  if (route.path.startsWith("/hive/demandes")) {
    return "demandes";
  }
  return "catalogue";
});

const showGossipBar = computed(() => hiveEnabled.value && nextGossipMs.value != null);

function applyOverview(body: HiveOverview) {
  hiveEnabled.value = body.enabled;
  if (body.enabled) {
    nextGossipMs.value = parseIsoUtc(body.next_gossip_at);
    lastGossipMs.value = parseIsoUtc(body.last_gossip_at);
  } else {
    nextGossipMs.value = null;
    lastGossipMs.value = null;
  }
}

function rememberDueGuard() {
  const next = nextGossipMs.value;
  // Échéance déjà passée, nulle ou inchangée : pas de nouveau rechargement.
  if (next == null || Date.now() >= next) {
    dueHandledForMs = next;
  } else {
    dueHandledForMs = null;
  }
}

async function refreshOverview() {
  try {
    const overview = await api<HiveOverview>("/api/v1/hive/overview");
    if (!alive) {
      return;
    }
    applyOverview(overview);
    rememberDueGuard();
  } catch {
    if (!alive) {
      return;
    }
    hiveEnabled.value = false;
    nextGossipMs.value = null;
    lastGossipMs.value = null;
    dueHandledForMs = null;
  }
}

async function refreshCounts() {
  try {
    const [mine, received, wanted] = await Promise.all([
      api<{ items: HiveMyRequestItem[] }>("/api/v1/hive/requests/mine"),
      api<{ items: HiveReceivedRequestItem[] }>("/api/v1/hive/requests/received"),
      api<{ items: HiveWantedRequestItem[] }>("/api/v1/hive/requests/wanted"),
    ]);
    mineCount.value = mine.items?.length ?? 0;
    receivedCount.value = received.items?.length ?? 0;
    wantedCount.value = wanted.items?.length ?? 0;
  } catch {
    mineCount.value = null;
    receivedCount.value = null;
    wantedCount.value = null;
  }
}

async function reloadActiveTab() {
  const view = activeViewRef.value;
  if (view?.reload) {
    await view.reload();
  }
}

function clearRetryTimer() {
  if (retryTimer !== undefined) {
    clearTimeout(retryTimer);
    retryTimer = undefined;
  }
}

function nextStillStuck(previousNext: number | null): boolean {
  if (!hiveEnabled.value) {
    return false;
  }
  const next = nextGossipMs.value;
  if (next == null || Date.now() >= next) {
    return true;
  }
  return previousNext != null && next === previousNext;
}

function maybeScheduleSingleRetry(dueMs: number) {
  if (!alive || !nextStillStuck(dueMs) || retryUsedForMs === dueMs) {
    return;
  }
  retryUsedForMs = dueMs;
  clearRetryTimer();
  retryTimer = setTimeout(() => {
    retryTimer = undefined;
    if (!alive) {
      return;
    }
    void onGossipRetry();
  }, HIVE_GOSSIP_RETRY_MS);
}

async function reloadOverviewAndLists() {
  await refreshOverview();
  if (!alive) {
    return;
  }
  await Promise.all([refreshCounts(), reloadActiveTab()]);
}

async function onGossipDue() {
  if (!alive || gossipReloading.value) {
    return;
  }
  const dueMs = nextGossipMs.value;
  gossipReloading.value = true;
  try {
    await reloadOverviewAndLists();
    if (!alive || dueMs == null) {
      return;
    }
    maybeScheduleSingleRetry(dueMs);
  } finally {
    gossipReloading.value = false;
  }
}

async function onGossipRetry() {
  if (!alive || gossipReloading.value) {
    return;
  }
  gossipReloading.value = true;
  try {
    await reloadOverviewAndLists();
  } finally {
    gossipReloading.value = false;
  }
}

function onGossipTick() {
  const current = Date.now();
  nowMs.value = current;
  const next = nextGossipMs.value;
  if (next == null) {
    return;
  }
  if (current >= next) {
    if (dueHandledForMs !== next) {
      dueHandledForMs = next;
      void onGossipDue();
    }
  }
}

function tabBadge(id: (typeof tabs)[number]["id"]): number | null {
  if (id === "demandes") {
    return mineCount.value;
  }
  if (id === "demandes-autres") {
    if (receivedCount.value == null && wantedCount.value == null) {
      return null;
    }
    return (receivedCount.value ?? 0) + (wantedCount.value ?? 0);
  }
  return null;
}

provide(hiveShellContextKey, { refreshTabBadges: refreshCounts });

onMounted(async () => {
  await Promise.all([refreshOverview(), refreshCounts()]);
  if (!alive) {
    return;
  }
  tickTimer = setInterval(onGossipTick, 1000);
});

onUnmounted(() => {
  alive = false;
  if (tickTimer !== undefined) {
    clearInterval(tickTimer);
    tickTimer = undefined;
  }
  clearRetryTimer();
});

watch(
  () => route.path,
  () => {
    if (
      route.path.startsWith("/hive/demandes-autres") ||
      route.path.startsWith("/hive/recues") ||
      route.path.startsWith("/hive/recherche") ||
      route.path.startsWith("/hive/demandes")
    ) {
      void refreshCounts();
    }
  },
);
</script>

<template>
  <div class="hive-shell zone">
    <header class="hive-head row sp">
      <h1>Hive</h1>
      <RouterLink class="btn btn-sm" to="/reglages/hive">Réglages Hive</RouterLink>
    </header>
    <HiveGossipSyncBar
      v-if="showGossipBar"
      :next-ms="nextGossipMs"
      :last-ms="lastGossipMs"
      :now-ms="nowMs"
    />
    <nav class="hive-tabs" aria-label="Sections Hive">
      <RouterLink
        v-for="tab in tabs"
        :key="tab.id"
        :to="tab.to"
        class="hive-tab"
        :class="{ active: activeTab === tab.id }"
        :data-testid="`hive-tab-${tab.id}`"
      >
        {{ tab.label }}
        <span v-if="tabBadge(tab.id) != null" class="hive-tab-badge" aria-label="Nombre">{{ tabBadge(tab.id) }}</span>
      </RouterLink>
    </nav>
    <RouterView v-slot="{ Component }">
      <component :is="Component" ref="activeViewRef" />
    </RouterView>
  </div>
</template>

<style scoped>
.hive-head {
  margin-bottom: 0.75rem;
}

.hive-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin-bottom: 1rem;
}

.hive-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  min-height: var(--target-min);
  padding: 0.35rem 0.85rem;
  border-radius: 999px;
  border: 1px solid var(--hair);
  color: var(--muted);
  text-decoration: none;
  background: color-mix(in srgb, var(--bg) 85%, var(--card));
}

.hive-tab.active {
  color: var(--text);
  border-color: color-mix(in srgb, var(--accent) 45%, var(--hair));
  background: color-mix(in srgb, var(--accent) 12%, var(--card));
  font-weight: 700;
}

.hive-tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 1.35rem;
  height: 1.35rem;
  padding: 0 0.35rem;
  border-radius: 999px;
  font-size: 0.78rem;
  font-weight: 700;
  background: color-mix(in srgb, var(--honey, #d9b46a) 35%, var(--card));
  color: var(--text);
}

@media (max-width: 720px) {
  .hive-tabs {
    gap: 0.35rem;
  }

  .hive-tab {
    flex: 1 1 calc(50% - 0.35rem);
    justify-content: center;
    text-align: center;
  }
}
</style>
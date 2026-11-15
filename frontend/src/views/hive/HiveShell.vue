<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, RouterView, useRoute } from "vue-router";

import { api } from "../../api";
import type { HiveMyRequestItem } from "../../hive/hiveRequests";
import type { HiveReceivedRequestItem } from "../../hive/hiveRequests";
import type { HiveWantedRequestItem } from "../../hive/hiveRequests";

const route = useRoute();
const mineCount = ref<number | null>(null);
const receivedCount = ref<number | null>(null);
const wantedCount = ref<number | null>(null);

const tabs = [
  { id: "catalogue", label: "Catalogue", to: "/hive/catalogue" },
  { id: "demandes", label: "Mes demandes", to: "/hive/demandes" },
  { id: "recues", label: "Reçues", to: "/hive/recues" },
  { id: "recherche", label: "Recherché", to: "/hive/recherche" },
] as const;

const activeTab = computed(() => {
  if (route.path.startsWith("/hive/demandes")) {
    return "demandes";
  }
  if (route.path.startsWith("/hive/recues")) {
    return "recues";
  }
  if (route.path.startsWith("/hive/recherche")) {
    return "recherche";
  }
  return "catalogue";
});

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

function tabBadge(id: (typeof tabs)[number]["id"]): number | null {
  if (id === "demandes") {
    return mineCount.value;
  }
  if (id === "recues") {
    return receivedCount.value;
  }
  if (id === "recherche") {
    return wantedCount.value;
  }
  return null;
}

onMounted(refreshCounts);

watch(
  () => route.path,
  () => {
    if (
      route.path.startsWith("/hive/demandes") ||
      route.path.startsWith("/hive/recues") ||
      route.path.startsWith("/hive/recherche")
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
    <RouterView />
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
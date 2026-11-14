<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import { api } from "../api";
import ActionMenuPopover from "./ActionMenuPopover.vue";
import HiveBulkShareMenuItems from "./HiveBulkShareMenuItems.vue";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveShareFicheStatus } from "../hive/hiveShareStatus";
import { hiveRucheLabel, type HiveRucheListRow } from "../hive/hiveRucheLabel";

const props = defineProps<{
  hiveEnabled: boolean;
  episodeStates: Record<number, HiveShareUiState>;
  mediaId: number;
  /** inline : bandeau compact ; card : carte fiche série (maquettes A/B). */
  layout?: "inline" | "card";
}>();

const emit = defineEmits<{ changed: []; openEmpreintes: [] }>();

const legendOpen = ref(false);
const ruches = ref<HiveRucheListRow[]>([]);
const cardLayout = computed(() => (props.layout ?? "card") === "card");

const stats = computed(() => {
  let shared = 0;
  let preparing = 0;
  let errors = 0;
  for (const state of Object.values(props.episodeStates)) {
    if (!state?.has_file) {
      continue;
    }
    const phase = hiveShareFicheStatus(state, {}).phase;
    if (state.shared && !state.exclude_all) {
      shared += 1;
    } else if (phase === "preparing") {
      preparing += 1;
    } else if (phase === "error") {
      errors += 1;
    }
  }
  return { shared, preparing, errors };
});

const summaryLine = computed(() => {
  const parts: string[] = [];
  if (stats.value.shared) {
    parts.push(`${stats.value.shared} épisode${stats.value.shared > 1 ? "s" : ""} partagé${stats.value.shared > 1 ? "s" : ""}`);
  }
  if (stats.value.preparing) {
    parts.push(`${stats.value.preparing} en préparation`);
  }
  if (stats.value.errors) {
    parts.push(`${stats.value.errors} erreur${stats.value.errors > 1 ? "s" : ""}`);
  }
  return parts.join(" · ") || "Aucun épisode partagé";
});

const shareActive = computed(() => stats.value.shared > 0);

onMounted(async () => {
  if (!props.hiveEnabled || !cardLayout.value) {
    return;
  }
  try {
    ruches.value = await api<HiveRucheListRow[]>("/api/v1/hive/ruches");
  } catch {
    ruches.value = [];
  }
});
</script>

<template>
  <template v-if="hiveEnabled">
    <article
      v-if="cardLayout"
      class="panel fiche-hive-card"
      aria-label="Hive, partage"
      data-testid="hive-series-summary"
    >
      <svg class="fiche-hive-card-ico" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
        <path
          d="M12 2 18 6v6l-6 10-6-10V6Z"
          fill="none"
          stroke="currentColor"
          stroke-width="1.75"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <path
          d="M12 8v8"
          fill="none"
          stroke="currentColor"
          stroke-width="1.75"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
      </svg>
      <header class="fiche-hive-card-head">
        <span class="lbl hint">Hive</span>
        <span v-if="shareActive" class="pill honey">Partage actif</span>
        <span v-else class="pill muted">Inactif</span>
      </header>
      <p class="fiche-hive-card-meta">{{ summaryLine }}</p>
      <p v-if="ruches.length" class="fiche-hive-card-ruches">
        <span v-for="row in ruches" :key="row.ruche_id_hex" class="pill muted">{{ hiveRucheLabel(row) }}</span>
      </p>
      <div class="fiche-hive-card-act">
        <ActionMenuPopover
          label="Gérer le partage"
          trigger-variant="fiche"
          :show-status-icon="false"
          compact-on-phone
          menu-aria-label="Partage Hive pour la série"
        >
          <HiveBulkShareMenuItems
            :media-id="mediaId"
            :hive-enabled="hiveEnabled"
            @changed="emit('changed')"
            @open-empreintes="emit('openEmpreintes')"
          />
        </ActionMenuPopover>
      </div>
      <details
        class="fiche-hive-card-legend"
        :open="legendOpen"
        @toggle="legendOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary>Comprendre les états</summary>
      </details>
    </article>

    <div v-else class="hive-series-summary" data-testid="hive-series-summary">
      <span class="lbl hint">Hive</span>
      <span class="hive-series-chip tone-honey">{{ summaryLine }}</span>
      <ActionMenuPopover
        label="Actions"
        trigger-variant="toggle"
        compact-on-phone
        menu-aria-label="Partage Hive pour la série"
        class="hive-series-menu"
      >
        <HiveBulkShareMenuItems
          :media-id="mediaId"
          :hive-enabled="hiveEnabled"
          @changed="emit('changed')"
          @open-empreintes="emit('openEmpreintes')"
        />
      </ActionMenuPopover>
      <details
        class="hive-series-legend"
        :open="legendOpen"
        @toggle="legendOpen = ($event.target as HTMLDetailsElement).open"
      >
        <summary>Comprendre les états</summary>
      </details>
    </div>
  </template>
</template>

<style scoped>
.hive-series-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem 0.75rem;
  margin-top: 0.75rem;
}
.hive-series-chip {
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--honey, #d9b46a);
}
.hive-series-menu {
  margin-left: auto;
}
.hive-series-legend summary,
.fiche-hive-card-legend summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.92rem;
  text-decoration: underline;
}

.fiche-hive-card {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.35rem 0.75rem;
  padding: var(--space-4);
  align-items: start;
}

.fiche-hive-card-ico {
  grid-row: 1 / span 3;
  color: var(--honey, #d9b46a);
  margin-top: 0.1rem;
}

.fiche-hive-card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
  grid-column: 2;
}

.fiche-hive-card-head .lbl {
  font-weight: 700;
  color: inherit;
}

.pill.honey {
  color: var(--honey, #d9b46a);
  border-color: color-mix(in srgb, var(--honey, #d9b46a) 45%, transparent);
}

.fiche-hive-card-meta {
  margin: 0;
  grid-column: 2;
  font-size: 0.95rem;
}

.fiche-hive-card-ruches {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  grid-column: 2;
}

.fiche-hive-card-act {
  grid-column: 1 / -1;
  margin-top: 0.35rem;
}

.fiche-hive-card-act :deep(.action-menu-trigger) {
  width: 100%;
  justify-content: center;
}

.fiche-hive-card-legend {
  grid-column: 1 / -1;
  margin: 0.25rem 0 0;
}
</style>
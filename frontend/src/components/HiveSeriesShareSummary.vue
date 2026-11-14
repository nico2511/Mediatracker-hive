<script setup lang="ts">
import { computed, ref } from "vue";

import ActionMenuPopover from "./ActionMenuPopover.vue";
import HiveBulkShareMenuItems from "./HiveBulkShareMenuItems.vue";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveShareFicheStatus } from "../hive/hiveShareStatus";

const props = defineProps<{
  hiveEnabled: boolean;
  episodeStates: Record<number, HiveShareUiState>;
  mediaId: number;
}>();

const emit = defineEmits<{ changed: []; openEmpreintes: [] }>();

const legendOpen = ref(false);

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
</script>

<template>
  <div v-if="hiveEnabled" class="hive-series-summary" data-testid="hive-series-summary">
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
    <details class="hive-series-legend" :open="legendOpen" @toggle="legendOpen = ($event.target as HTMLDetailsElement).open">
      <summary>Comprendre les états</summary>
    </details>
  </div>
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
.hive-series-legend summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.92rem;
  text-decoration: underline;
}
</style>

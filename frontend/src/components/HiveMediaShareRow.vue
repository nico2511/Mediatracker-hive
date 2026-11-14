<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import ActionMenuPopover from "./ActionMenuPopover.vue";
import HiveContextMenuItems from "./HiveContextMenuItems.vue";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveShareFicheStatus } from "../hive/hiveShareStatus";

const props = defineProps<{
  state: HiveShareUiState | null;
  hiveEnabled: boolean;
  mediaItemId: number;
  mediaId: number;
  catalogueWorkKey?: string | null;
  requestError?: string | null;
  pollExhausted?: boolean;
  pollBusy?: boolean;
}>();

const emit = defineEmits<{ changed: []; refresh: [] }>();

const router = useRouter();
const legendOpen = ref(false);

const status = computed(() =>
  hiveShareFicheStatus(props.state, {
    requestError: props.requestError,
    pollExhausted: props.pollExhausted,
    pollBusy: props.pollBusy,
  }),
);

const fingerprintPct = computed(() => {
  if (!props.state?.preparing) {
    return null;
  }
  const partial = props.state.fingerprint_partial_size ?? 0;
  const total = props.state.fingerprint_total_size ?? 0;
  if (total <= 0) {
    return null;
  }
  if (partial >= total) {
    return 99;
  }
  return Math.min(99, Math.max(0, Math.round((100 * partial) / total)));
});

const showBar = computed(() => fingerprintPct.value != null || props.state?.transfer_active);

const barPct = computed(() => {
  if (props.state?.transfer_active && props.state.transfer_progress_pct != null) {
    return Math.round(props.state.transfer_progress_pct);
  }
  return fingerprintPct.value;
});

const lineDetail = computed(() => {
  if (status.value.phase === "preparing" && fingerprintPct.value != null) {
    return `Empreinte ${fingerprintPct.value} %`;
  }
  if (props.state?.transfer_active && props.state.transfer_progress_pct != null) {
    return `Transfert ${Math.round(props.state.transfer_progress_pct)} %`;
  }
  return "";
});

function openCatalogue() {
  if (props.catalogueWorkKey) {
    void router.push(`/hive/catalogue/${props.catalogueWorkKey}`);
  }
}

const legend = [
  { title: "Non partagé", text: "Partage manuel : rien n’est proposé sans action dans le menu." },
  { title: "Préparation", text: "Empreinte en cours avant annonce locale." },
  { title: "Partagé", text: "Annonce locale prête pour les ruches autorisées." },
  { title: "Erreur", text: "Lecture ou publication impossible ; voir le menu." },
];
</script>

<template>
  <article
    v-if="hiveEnabled && status.phase !== 'hidden'"
    class="panel hive-media-share-row"
    aria-live="polite"
    data-testid="hive-media-share-row"
  >
    <svg class="hive-media-share-ico" viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
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
    <div class="hive-media-share-main">
      <p class="hive-media-share-title">
        <span class="lbl hint">Hive</span>
        <strong>{{ status.title }}</strong>
        <span v-if="pollBusy" class="hint"> · mise à jour…</span>
      </p>
      <p v-if="lineDetail || status.hint" class="hive-media-share-line hint">
        <span v-if="lineDetail">{{ lineDetail }}</span>
        <span v-if="showBar" class="hive-media-share-bar" aria-hidden="true">
          <i :style="{ width: `${barPct ?? 0}%` }" />
        </span>
        <span v-if="status.hint && status.phase !== 'preparing'">{{ status.hint }}</span>
      </p>
    </div>
    <ActionMenuPopover label="Partage" trigger-variant="toggle" compact-on-phone menu-aria-label="Menu partage Hive">
      <HiveContextMenuItems
        :media-item-id="mediaItemId"
        :media-id="mediaId"
        :hive-enabled="hiveEnabled"
        :versions-to="catalogueWorkKey ? `/hive/catalogue/${catalogueWorkKey}` : null"
        @changed="emit('changed')"
      />
      <button
        v-if="catalogueWorkKey"
        type="button"
        class="action-menu-item"
        role="menuitem"
        @click="openCatalogue"
      >
        Voir dans le catalogue Hive
      </button>
      <button
        v-if="pollExhausted || requestError"
        type="button"
        class="action-menu-item"
        role="menuitem"
        @click="emit('refresh')"
      >
        Actualiser
      </button>
      <p class="hint-row action-menu-hint" role="none">
        Partage manuel : rien n’est partagé sans votre accord.
      </p>
    </ActionMenuPopover>
    <details class="hive-media-share-legend" :open="legendOpen" @toggle="legendOpen = ($event.target as HTMLDetailsElement).open">
      <summary>Comprendre les états</summary>
      <ul>
        <li v-for="row in legend" :key="row.title">
          <strong>{{ row.title }}</strong>
          <span class="hint"> — {{ row.text }}</span>
        </li>
      </ul>
    </details>
  </article>
</template>

<style scoped>
.hive-media-share-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.65rem 1rem;
  padding: 0.75rem 1rem;
}

.hive-media-share-ico {
  color: var(--honey, #d9b46a);
  flex-shrink: 0;
}

.hive-media-share-main {
  flex: 1 1 12rem;
  min-width: 0;
}

.hive-media-share-title {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.35rem;
}

.hive-media-share-title .lbl {
  font-size: 0.85rem;
}

.hive-media-share-line {
  margin: 0.25rem 0 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.5rem;
}

.hive-media-share-bar {
  display: inline-block;
  width: 5rem;
  height: 0.35rem;
  background: rgb(255 255 255 / 12%);
  border-radius: 2px;
  vertical-align: middle;
}

.hive-media-share-bar > i {
  display: block;
  height: 100%;
  background: var(--honey, #d9b46a);
  border-radius: 2px;
}

.hive-media-share-legend {
  flex: 1 1 100%;
  margin: 0;
}

.hive-media-share-legend summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.92rem;
  text-decoration: underline;
}

.hive-media-share-legend ul {
  margin: 0.5rem 0 0;
  padding-left: 1.1rem;
}
</style>

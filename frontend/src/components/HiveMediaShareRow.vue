<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import ActionMenuPopover from "./ActionMenuPopover.vue";
import HiveContextMenuItems from "./HiveContextMenuItems.vue";
import HiveEmpreintesPanel from "./HiveEmpreintesPanel.vue";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveShareFicheStatus } from "../hive/hiveShareStatus";
import { hiveRucheLabel, type HiveRucheListRow } from "../hive/hiveRucheLabel";
import { api } from "../api";

const props = defineProps<{
  state: HiveShareUiState | null;
  hiveEnabled: boolean;
  mediaItemId: number;
  mediaId: number;
  catalogueWorkKey?: string | null;
  requestError?: string | null;
  pollExhausted?: boolean;
  pollBusy?: boolean;
  openSignal?: number;
}>();

const emit = defineEmits<{ changed: []; refresh: [] }>();

const router = useRouter();
const legendOpen = ref(false);
const empreintesFoldOpen = ref(false);
const ruches = ref<HiveRucheListRow[]>([]);

const status = computed(() =>
  hiveShareFicheStatus(props.state, {
    requestError: props.requestError,
    pollExhausted: props.pollExhausted,
    pollBusy: props.pollBusy,
  }),
);

const shareActive = computed(() => Boolean(props.state?.shared && !props.state?.exclude_all));

const summaryLine = computed(() => {
  if (status.value.phase === "preparing") {
    return status.value.title;
  }
  if (shareActive.value) {
    return "Partagé · fichier sur disque";
  }
  if (status.value.phase === "not_shared" || status.value.phase === "intention") {
    return "Non partagé";
  }
  if (status.value.title) {
    return status.value.title;
  }
  return "Non partagé";
});

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
  if (status.value.hint && status.value.phase !== "preparing") {
    return status.value.hint;
  }
  return "";
});

function openCatalogue() {
  if (props.catalogueWorkKey) {
    void router.push(`/hive/catalogue/${props.catalogueWorkKey}`);
  }
}

function onEmpreintesFoldToggle(event: Event) {
  empreintesFoldOpen.value = (event.target as HTMLDetailsElement).open;
}

watch(
  () => props.openSignal,
  () => {
    empreintesFoldOpen.value = true;
  },
);

onMounted(async () => {
  if (!props.hiveEnabled) {
    return;
  }
  try {
    ruches.value = await api<HiveRucheListRow[]>("/api/v1/hive/ruches");
  } catch {
    ruches.value = [];
  }
});

const legend = [
  { title: "Non partagé", text: "Partage manuel : rien n’est proposé sans action dans le menu." },
  { title: "Préparation", text: "Empreinte en cours avant annonce locale." },
  { title: "Partagé", text: "Annonce locale prête pour les ruches autorisées." },
  { title: "Erreur", text: "Lecture ou publication impossible ; voir le menu." },
];
</script>

<template>
  <article
    v-if="hiveEnabled"
    class="panel fiche-hive-card fiche-fold fiche-fold--hive"
    aria-live="polite"
    data-testid="hive-fiche-card"
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
      <span class="lbl">Hive</span>
      <span v-if="shareActive" class="pill honey">Partage actif</span>
      <span v-else class="pill muted">Inactif</span>
      <span v-if="pollBusy" class="hint"> · mise à jour…</span>
    </header>
    <p class="fiche-hive-card-meta">{{ summaryLine }}</p>
    <p v-if="lineDetail || showBar" class="fiche-hive-card-meta hint">
      <span v-if="lineDetail">{{ lineDetail }}</span>
      <span v-if="showBar" class="hive-media-share-bar" aria-hidden="true">
        <i :style="{ width: `${barPct ?? 0}%` }" />
      </span>
    </p>
    <p v-if="ruches.length" class="fiche-hive-card-ruches">
      <span v-for="row in ruches" :key="row.ruche_id_hex" class="pill muted">{{ hiveRucheLabel(row) }}</span>
    </p>
    <div class="fiche-hive-card-act">
      <ActionMenuPopover
        label="Gérer le partage"
        trigger-variant="fiche"
        :show-status-icon="false"
        compact-on-phone
        menu-aria-label="Menu partage Hive"
      >
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
    </div>
    <details
      class="fiche-hive-fold"
      data-testid="hive-empreintes-fold"
      :open="empreintesFoldOpen"
      @toggle="onEmpreintesFoldToggle"
    >
      <summary>Empreintes et aide</summary>
      <div class="fiche-hive-fold-body">
        <HiveEmpreintesPanel
          embedded
          :active="empreintesFoldOpen"
          :media-id="mediaId"
          :hive-enabled="hiveEnabled"
          @update="emit('changed')"
        />
        <details
          class="fiche-hive-legend"
          :open="legendOpen"
          @toggle="legendOpen = ($event.target as HTMLDetailsElement).open"
        >
          <summary>Comprendre les états</summary>
          <ul>
            <li v-for="row in legend" :key="row.title">
              <strong>{{ row.title }}</strong>
              <span class="hint"> — {{ row.text }}</span>
            </li>
          </ul>
        </details>
      </div>
    </details>
  </article>
</template>

<style scoped>
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

.fiche-hive-fold {
  grid-column: 1 / -1;
  margin: 0.35rem 0 0;
  border-top: 1px solid var(--line);
  padding-top: 0.25rem;
}

.fiche-hive-fold summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.92rem;
  min-height: 2.75rem;
  display: flex;
  align-items: center;
  list-style: none;
}

.fiche-hive-fold summary::-webkit-details-marker {
  display: none;
}

.fiche-hive-fold-body {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
  padding-bottom: 0.35rem;
}

.fiche-hive-legend summary {
  cursor: pointer;
  color: var(--muted);
  font-size: 0.92rem;
  text-decoration: underline;
}

.fiche-hive-legend ul {
  margin: 0.5rem 0 0;
  padding-left: 1.1rem;
}

@media (min-width: 721px) {
  .fiche-hive-card {
    grid-template-columns: 20px minmax(0, 1fr) auto;
  }

  .fiche-hive-card-ico {
    grid-row: 1 / span 3;
  }

  .fiche-hive-card-act {
    grid-column: 3;
    grid-row: 1 / span 3;
    margin-top: 0;
    align-self: start;
  }

  .fiche-hive-card-act :deep(.action-menu-trigger) {
    width: auto;
    min-width: 11.5rem;
  }

  .fiche-hive-card-ruches,
  .fiche-hive-fold {
    grid-column: 2 / -1;
  }
}
</style>
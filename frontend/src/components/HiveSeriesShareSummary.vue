<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api, userFacingApiError } from "../api";
import ActionMenuPopover from "./ActionMenuPopover.vue";
import HiveBulkShareMenuItems from "./HiveBulkShareMenuItems.vue";
import HiveEmpreintesPanel from "./HiveEmpreintesPanel.vue";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveShareFicheStatus } from "../hive/hiveShareStatus";
import { hiveRucheLabel, type HiveRucheListRow } from "../hive/hiveRucheLabel";
import {
  hiveFicheSeriesDemandLine,
  hiveMediaRequestsActiveAnnounced,
  type HiveMyRequestItem,
} from "../hive/hiveRequests";

const props = defineProps<{
  hiveEnabled: boolean;
  episodeStates: Record<number, HiveShareUiState>;
  mediaId: number;
  /** inline : bandeau compact ; card : carte fiche série (maquettes A/B). */
  layout?: "inline" | "card";
  mediaRequests?: HiveMyRequestItem[];
  openSignal?: number;
}>();

const emit = defineEmits<{ changed: [] }>();

defineOptions({ inheritAttrs: false });

const router = useRouter();
const legendOpen = ref(false);
const empreintesFoldOpen = ref(false);
const ruches = ref<HiveRucheListRow[]>([]);
const requestError = ref("");
const requestBusy = ref(false);

const legend = [
  { title: "Non partagé", text: "Partage manuel : rien n’est proposé sans action dans le menu." },
  { title: "Préparation", text: "Empreinte en cours avant annonce locale." },
  { title: "Partagé", text: "Annonce locale prête pour les ruches autorisées." },
  { title: "Erreur", text: "Lecture ou publication impossible ; voir le menu." },
];

function onEmpreintesFoldToggle(event: Event) {
  empreintesFoldOpen.value = (event.target as HTMLDetailsElement).open;
}

watch(
  () => props.openSignal,
  () => {
    empreintesFoldOpen.value = true;
  },
);
const cardLayout = computed(() => (props.layout ?? "card") === "card");

const mediaRequests = computed(() => props.mediaRequests ?? []);

const stats = computed(() => {
  let shared = 0;
  let preparing = 0;
  let errors = 0;
  let withFile = 0;
  for (const state of Object.values(props.episodeStates)) {
    if (!state?.has_file) {
      continue;
    }
    withFile += 1;
    const phase = hiveShareFicheStatus(state, {}).phase;
    if (state.shared && !state.exclude_all) {
      shared += 1;
    } else if (phase === "preparing") {
      preparing += 1;
    } else if (phase === "error") {
      errors += 1;
    }
  }
  return { shared, preparing, errors, withFile };
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

const partageLine = computed(() => {
  const y = stats.value.withFile;
  const x = stats.value.shared;
  if (y === 0) {
    return "Aucun épisode partagé";
  }
  const unit = y > 1 ? "épisodes" : "épisode";
  const sharedWord = x > 1 ? "partagés" : "partagé";
  return `${x}/${y} ${unit} ${sharedWord}`;
});

const demandLine = computed(() => hiveFicheSeriesDemandLine(mediaRequests.value));

const showDemandSection = computed(() => mediaRequests.value.length > 0);

const shareActive = computed(() => stats.value.shared > 0);

const canRetractAll = computed(() => hiveMediaRequestsActiveAnnounced(mediaRequests.value).length > 0);

async function retractAllRequests() {
  const targets = hiveMediaRequestsActiveAnnounced(mediaRequests.value);
  if (!targets.length) {
    return;
  }
  requestBusy.value = true;
  requestError.value = "";
  try {
    for (const row of targets) {
      await api("/api/v1/hive/requests/retract", {
        method: "POST",
        body: JSON.stringify({ media_item_id: row.media_item_id }),
      });
    }
    emit("changed");
  } catch (err) {
    requestError.value = userFacingApiError(err, "Retrait impossible.");
  } finally {
    requestBusy.value = false;
  }
}

function openMineRequests() {
  void router.push("/hive/demandes");
}

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
  <article
    v-if="hiveEnabled && cardLayout"
    v-bind="$attrs"
    class="panel fiche-hive-card fiche-fold fiche-fold--hive"
    aria-label="Hive, partage"
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
        <span class="lbl hint">Hive</span>
        <span v-if="shareActive" class="pill honey">Partage actif</span>
        <span v-else class="pill muted">Inactif</span>
      </header>
      <section class="fiche-hive-section" data-testid="hive-fiche-partage">
        <p class="fiche-hive-kicker hint">Partage</p>
        <p class="fiche-hive-section-line fiche-hive-card-meta">
          <span data-testid="hive-fiche-partage-line">{{ partageLine }}</span>
          <span class="fiche-hive-inline-sep" aria-hidden="true">·</span>
          <ActionMenuPopover
            label="Gérer"
            trigger-variant="fiche"
            :show-status-icon="false"
            compact-on-phone
            menu-aria-label="Partage Hive pour la série"
            class="fiche-hive-partage-gerer"
          >
            <HiveBulkShareMenuItems
              :media-id="mediaId"
              :hive-enabled="hiveEnabled"
              @changed="emit('changed')"
              @open-empreintes="empreintesFoldOpen = true"
            />
          </ActionMenuPopover>
        </p>
      </section>
      <section
        v-if="showDemandSection"
        class="fiche-hive-section"
        data-testid="hive-fiche-demande"
      >
        <p class="fiche-hive-kicker hint">Demande</p>
        <p class="fiche-hive-section-line hint" data-testid="hive-fiche-demande-line">{{ demandLine }}</p>
        <p v-if="requestError" class="error fiche-hive-section-line" role="alert">{{ requestError }}</p>
        <div class="fiche-hive-demand-actions">
          <button
            type="button"
            class="btn"
            data-testid="hive-fiche-request-open-mine"
            :disabled="requestBusy"
            @click="openMineRequests"
          >
            Voir mes demandes
          </button>
          <button
            v-if="canRetractAll"
            type="button"
            class="btn linkish"
            data-testid="hive-fiche-request-retract-all"
            :disabled="requestBusy"
            @click="retractAllRequests"
          >
            Ne plus demander
          </button>
        </div>
      </section>
      <p v-if="ruches.length" class="fiche-hive-card-ruches">
        <span v-for="row in ruches" :key="row.ruche_id_hex" class="pill muted">{{ hiveRucheLabel(row) }}</span>
      </p>
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

  <div
    v-else-if="hiveEnabled"
    v-bind="$attrs"
    class="hive-series-summary"
    data-testid="hive-series-summary"
  >
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
          @open-empreintes="empreintesFoldOpen = true"
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

.fiche-hive-card {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 0.35rem 0.75rem;
  padding: var(--space-4);
  align-items: start;
}

.fiche-hive-card-ico {
  grid-row: 1 / span 4;
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
  font-size: 0.95rem;
}

.fiche-hive-kicker {
  margin: 0;
  font-size: 0.72rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.fiche-hive-section {
  grid-column: 2;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.fiche-hive-section-line {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.5rem;
}

.fiche-hive-inline-sep {
  color: var(--muted);
}

.fiche-hive-partage-gerer :deep(.action-menu-trigger) {
  width: auto;
  min-width: 0;
  min-height: var(--target-min);
  padding: 0;
  justify-content: flex-start;
}

.fiche-hive-card-ruches {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  grid-column: 2;
}

.fiche-hive-demand-actions {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 0.45rem;
  margin-top: 0.2rem;
}

.btn.linkish {
  background: transparent;
  border: 0;
  color: var(--muted);
  text-decoration: underline;
  min-height: var(--target-min);
}

@media (min-width: 721px) {
  .fiche-hive-card {
    grid-template-columns: 20px minmax(0, 1fr);
  }

  .fiche-hive-card-ico {
    grid-row: 1 / span 5;
  }

  .fiche-hive-demand-actions {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
  }

  .fiche-hive-card-ruches,
  .fiche-hive-fold {
    grid-column: 2 / -1;
  }
}
</style>
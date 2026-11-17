<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from "vue";

import { api, userFacingApiError } from "../api";
import { PANEL_POLL_INTERVAL_MS } from "../constants/uiPollIntervals";
import {
  empreintePhaseClass,
  empreintePhaseLabel,
  type EmpreintesPage,
} from "../hive/hiveEmpreintes";

const props = defineProps<{
  mediaId: number;
  season?: number | null;
  hiveEnabled: boolean;
  openSignal?: number;
  /** Contenu affiché dans le dépliant « Empreintes et aide » du bloc Hive (sans <details> propre). */
  embedded?: boolean;
  /** Charge les données quand le dépliant parent est ouvert (mode embedded). */
  active?: boolean;
}>();

const emit = defineEmits<{ update: [] }>();

const panelOpen = ref(false);
const data = ref<EmpreintesPage | null>(null);
const error = ref("");
const busy = ref(false);
const confirmRecalc = ref(false);
const recalcNote = ref("");
let pollTimer: ReturnType<typeof setInterval> | null = null;

const query = computed(() => {
  if (props.season != null) {
    return `?season=${props.season}`;
  }
  return "";
});

const hasReadableFiles = computed(() => (data.value?.total ?? 0) > 0);

function stopPoll() {
  if (pollTimer != null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function pollAllowed(): boolean {
  if (props.embedded) {
    return Boolean(props.active);
  }
  return panelOpen.value;
}

function startPoll() {
  stopPoll();
  if (!pollAllowed()) {
    return;
  }
  pollTimer = setInterval(() => void load({ quiet: true }), PANEL_POLL_INTERVAL_MS);
}

async function load(opts?: { quiet?: boolean }) {
  if (!props.hiveEnabled) {
    data.value = null;
    return;
  }
  if (!opts?.quiet) {
    error.value = "";
  }
  try {
    data.value = await api<EmpreintesPage>(`/api/v1/hive/media/${props.mediaId}/empreintes${query.value}`);
  } catch (err) {
    if (!opts?.quiet) {
      data.value = null;
      error.value = userFacingApiError(err, "Impossible de charger les empreintes.");
    }
  }
}

watch(
  () => props.openSignal,
  () => {
    if (props.embedded) {
      return;
    }
    panelOpen.value = true;
    void load();
    startPoll();
  },
);

watch(
  () => props.active,
  (isActive) => {
    if (!props.embedded) {
      return;
    }
    if (isActive) {
      void load();
      startPoll();
    } else {
      stopPoll();
    }
  },
);

function onToggle(event: Event) {
  panelOpen.value = (event.target as HTMLDetailsElement).open;
  if (panelOpen.value) {
    void load();
    startPoll();
  } else {
    stopPoll();
  }
}

onUnmounted(stopPoll);

async function verify() {
  busy.value = true;
  error.value = "";
  try {
    data.value = await api<EmpreintesPage>(
      `/api/v1/hive/media/${props.mediaId}/empreintes/verify${query.value}`,
      { method: "POST" },
    );
  } catch (err) {
    error.value = userFacingApiError(err, "Contrôle impossible.");
  } finally {
    busy.value = false;
  }
}

async function recalc() {
  busy.value = true;
  error.value = "";
  recalcNote.value = "";
  try {
    const body = await api<{
      scheduled_chunks: number;
      in_progress_chunks: number;
      file_sha_queued: number;
      skipped_no_fingerprint: number;
      wake_sent: boolean;
    }>(`/api/v1/hive/media/${props.mediaId}/empreintes/recalculate${query.value}`, {
      method: "POST",
    });
    confirmRecalc.value = false;
    if (body.scheduled_chunks > 0) {
      recalcNote.value = `${body.scheduled_chunks} fichier(s) morceaux programmé(s).`;
    } else if (body.in_progress_chunks > 0) {
      recalcNote.value = "Calcul morceaux déjà en cours sur ce périmètre.";
    } else if (body.file_sha_queued > 0) {
      recalcNote.value = "Empreinte fichier mise en file.";
    } else if (body.skipped_no_fingerprint > 0) {
      recalcNote.value = "Empreinte fichier absente : démarrez le partage ou attendez la préparation.";
    } else if (!body.wake_sent) {
      recalcNote.value = "Aucun recalcul éligible (partage requis ou morceaux déjà prêts).";
    }
    await load();
    startPoll();
    emit("update");
  } catch (err) {
    error.value = userFacingApiError(err, "Recalcul impossible.");
  } finally {
    busy.value = false;
  }
}

const summaryLine = computed(() => {
  if (!data.value) {
    return "";
  }
  const { ready_count, pending_count, total } = data.value;
  if (!total) {
    return "Aucun fichier local dans ce périmètre.";
  }
  return `${ready_count} prêtes · ${pending_count} en attente ou à contrôler`;
});

const empreintesTeaser = computed(() => {
  if (!data.value?.total) {
    return "Aucun fichier à empreindre pour l'instant.";
  }
  const ready = data.value.ready_count;
  if (ready > 0) {
    return `${ready} fichier${ready > 1 ? "s" : ""} avec empreinte à jour.`;
  }
  return summaryLine.value;
});

function chunkLine(row: EmpreintesPage["rows"][number]): string {
  if (row.chunk_progress_pct != null && row.chunk_state === "calculating") {
    return `${row.chunk_detail} (${row.chunk_progress_pct} %)`;
  }
  return row.chunk_detail;
}
</script>

<template>
  <details
    v-if="hiveEnabled && !embedded"
    class="hive-empreintes panel"
    :open="panelOpen"
    data-testid="hive-empreintes-standalone"
    @toggle="onToggle"
  >
    <summary>Détails des empreintes</summary>
    <p class="hint">
      Empreinte fichier (SHA catalogue pour les réceptions Hive). Les morceaux 16&nbsp;Mio ne sont plus
      calculés ni exigés pour les transferts. Vérifier contrôle la cohérence des métadonnées en base
      — pas une certification du disque.
    </p>
    <p v-if="data" class="hint">{{ summaryLine }}</p>
    <p v-if="recalcNote" class="hint">{{ recalcNote }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul v-if="data?.rows.length" class="hive-empreintes-list">
      <li v-for="row in data.rows" :key="row.media_item_id" class="row sp">
        <span class="slot">{{ row.slot_label }}</span>
        <span class="hint file-col">{{ row.file_detail }}</span>
        <span class="hint chunk-col">{{ chunkLine(row) }}</span>
        <span class="pill" :class="empreintePhaseClass(row.phase)">{{ empreintePhaseLabel(row.phase) }}</span>
      </li>
    </ul>
    <p class="hint">Le calcul lit le disque. Les fichiers ne sont pas modifiés.</p>
    <div class="row wrap hive-empreintes-actions">
      <button type="button" class="btn" :disabled="busy || !hasReadableFiles" @click="verify">Vérifier</button>
      <button
        type="button"
        class="btn outline-honey"
        :disabled="busy || !hasReadableFiles"
        @click="confirmRecalc = true"
      >
        Recalculer…
      </button>
    </div>
    <p v-if="!hasReadableFiles && data" class="hint">Aucun fichier local : actions d’empreintes indisponibles.</p>
  </details>

  <div
    v-else-if="hiveEnabled && embedded"
    class="hive-empreintes-embedded"
    data-testid="hive-empreintes-embedded"
  >
    <p v-if="active || data" class="hint hive-empreintes-teaser">{{ empreintesTeaser }}</p>
    <template v-if="active">
      <p class="hint">
        Empreinte fichier (SHA catalogue pour les réceptions Hive). Les morceaux 16&nbsp;Mio ne sont plus
        calculés ni exigés pour les transferts. Vérifier contrôle la cohérence des métadonnées en base
        — pas une certification du disque.
      </p>
      <p v-if="recalcNote" class="hint">{{ recalcNote }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <ul v-if="data?.rows.length" class="hive-empreintes-list">
        <li v-for="row in data.rows" :key="row.media_item_id" class="row sp">
          <span class="slot">{{ row.slot_label }}</span>
          <span class="hint file-col">{{ row.file_detail }}</span>
          <span class="hint chunk-col">{{ chunkLine(row) }}</span>
          <span class="pill" :class="empreintePhaseClass(row.phase)">{{ empreintePhaseLabel(row.phase) }}</span>
        </li>
      </ul>
      <p class="hint">Le calcul lit le disque. Les fichiers ne sont pas modifiés.</p>
      <div class="row wrap hive-empreintes-actions">
        <button type="button" class="btn" :disabled="busy || !hasReadableFiles" @click="verify">Vérifier</button>
        <button
          type="button"
          class="btn outline-honey"
          :disabled="busy || !hasReadableFiles"
          @click="confirmRecalc = true"
        >
          Recalculer…
        </button>
      </div>
      <p v-if="!hasReadableFiles && data" class="hint">Aucun fichier local : actions d’empreintes indisponibles.</p>
    </template>
  </div>

  <Teleport to="body">
    <div
      v-if="confirmRecalc"
      class="hive-picker-backdrop"
      role="presentation"
      @click.self="confirmRecalc = false"
    >
      <div class="hive-picker panel dialog" role="dialog" aria-modal="true">
        <h3>Recalculer les empreintes</h3>
        <p class="hint">
          Programme le calcul de l’empreinte fichier pour les fichiers partagés de ce périmètre (les morceaux ne sont
          plus utilisés pour les transferts).
        </p>
        <div class="row sp">
          <button type="button" class="btn primary" :disabled="busy" @click="recalc">Confirmer</button>
          <button type="button" class="btn" @click="confirmRecalc = false">Annuler</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.hive-empreintes {
  margin-top: 0.75rem;
}
.hive-empreintes-list {
  list-style: none;
  padding: 0;
  margin: 0.5rem 0;
  display: grid;
  gap: 0.35rem;
}
.hive-empreintes-list li {
  flex-wrap: wrap;
  gap: 0.35rem 0.75rem;
  align-items: baseline;
}
.slot {
  font-weight: 700;
  min-width: 4.5rem;
}
.file-col,
.chunk-col {
  flex: 1 1 8rem;
}
.hive-empreintes-actions {
  gap: 0.5rem;
  margin-top: 0.5rem;
}
.outline-honey {
  border-color: var(--honey, #d9b46a);
  color: var(--honey, #d9b46a);
}
.hive-picker-backdrop {
  position: fixed;
  inset: 0;
  z-index: 2500;
  display: grid;
  place-items: center;
  padding: var(--space-4);
  background: rgb(0 0 0 / 55%);
}
.hive-picker {
  width: min(440px, 100%);
  padding: 1rem;
  background: var(--card);
}
</style>

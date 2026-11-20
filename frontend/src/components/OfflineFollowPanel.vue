<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { mediaKind } from "../labels";
import { formatParis, formatParisClock } from "../time";
import { useConnectivity } from "../pwa/connectivity";
import { listQueueItems, removeQueueItem, type QueueItem } from "../pwa/offlineQueue";
import { submitOfflineFollowAdd } from "../pwa/offlineFollowAdd";
import { type FollowMediaType } from "../pwa/parseTmdbInput";
import { alternateFollowType } from "../pwa/parseTmdbInput";
import type { SyncResultRow } from "../pwa/offlineSync";
import { updateQueueItemType } from "../pwa/offlineQueue";
import { fixedRetentionSnapshot } from "../fixedRetention";

const route = useRoute();
const router = useRouter();
const {
  mode,
  queueCount,
  refreshQueueCount,
  syncResults,
  clearSyncResults,
  tryFlushQueue,
  retryConnection,
  lastProbeAt,
} = useConnectivity();

const items = ref<QueueItem[]>([]);
const input = ref("");
const kind = ref<FollowMediaType>("series");
const duplicate = ref<QueueItem | null>(null);
const highlightId = ref<string | null>(null);
const pendingDelete = ref<{ id: string; label: string; timer: number } | null>(null);
const busy = ref(false);
const formError = ref("");

const showQueueOnly = computed(() => route.query.file === "1" || mode.value !== "online");
const results = computed(() => syncResults.value);
const showResults = computed(() => Boolean(results.value?.length));

const queueSubtitle = computed(() => {
  const n = items.value.length;
  const days = fixedRetentionSnapshot()?.source_state_days;
  const retention =
    typeof days === "number" ? `gardés ${days} jour${days > 1 ? "s" : ""}` : "conservation selon le serveur";
  return `${n} élément${n > 1 ? "s" : ""} · ${retention}`;
});

function addedLabel(item: QueueItem): string {
  return `Ajouté ${formatParis(item.added_at)} · ${formatParisClock(item.added_at)}`;
}

async function reload() {
  items.value = await listQueueItems();
  await refreshQueueCount();
}

onMounted(() => {
  void reload();
});

watch(
  () => route.query.file,
  () => {
    duplicate.value = null;
    highlightId.value = null;
  },
);

async function submitAdd() {
  formError.value = "";
  duplicate.value = null;
  highlightId.value = null;
  const result = await submitOfflineFollowAdd(input.value, kind.value);
  if (!result.ok) {
    if (result.reason === "invalid_input") {
      formError.value = "Indiquez un identifiant TMDB valide.";
      return;
    }
    if (result.reason === "duplicate") {
      duplicate.value = result.existing;
      highlightId.value = result.existing.id;
      return;
    }
    formError.value = "Identifiant invalide.";
    return;
  }
  input.value = "";
  await reload();
  void router.replace({ path: "/suivre", query: { file: "1" } });
}

function scheduleDelete(item: QueueItem) {
  if (pendingDelete.value) {
    window.clearTimeout(pendingDelete.value.timer);
  }
  const timer = window.setTimeout(() => {
    void removeQueueItem(item.id).then(reload);
    pendingDelete.value = null;
  }, 5000);
  pendingDelete.value = { id: item.id, label: `ID ${item.tmdb_id}`, timer };
}

function undoDelete() {
  if (!pendingDelete.value) {
    return;
  }
  window.clearTimeout(pendingDelete.value.timer);
  pendingDelete.value = null;
}

async function sendNow() {
  busy.value = true;
  try {
    await retryConnection();
    if (mode.value === "online") {
      await tryFlushQueue();
    }
  } finally {
    busy.value = false;
    await reload();
  }
}

async function tryAlternate(row: SyncResultRow) {
  const alt = alternateFollowType(row.item.type);
  await updateQueueItemType(row.item.id, alt);
  clearSyncResults();
  await tryFlushQueue();
  await reload();
}

async function removeResultRow(row: SyncResultRow) {
  await removeQueueItem(row.item.id);
  clearSyncResults();
  await reload();
}

function resultBadge(outcome: SyncResultRow["outcome"]): string {
  if (outcome === "added") {
    return "Ajouté";
  }
  if (outcome === "already") {
    return "Déjà suivi";
  }
  return "ID introuvable";
}

const lastTryLabel = computed(() => {
  const stamp = lastProbeAt.value;
  if (!stamp) {
    return "";
  }
  return `Dernier essai : ${formatParisClock(stamp)}, serveur injoignable.`;
});
</script>

<template>
  <section v-if="showResults" class="stack offline-follow">
    <h1>File envoyée</h1>
    <p class="hint">Envoi automatique au retour du serveur, {{ formatParis(new Date().toISOString()) }}.</p>
    <ul class="offline-sync-sum">
      <li v-for="row in results" :key="row.item.id">
        <span
          class="status-dot"
          :class="{
            ok: row.outcome === 'added',
            muted: row.outcome === 'already',
            danger: row.outcome === 'not_found',
          }"
        />
        {{ resultBadge(row) }}
      </li>
    </ul>
    <ul class="offline-sync-list">
      <li v-for="row in results" :key="`res-${row.item.id}`" class="offline-sync-row">
        <span class="poster-ghost-thumb" aria-hidden="true" />
        <div>
          <strong>{{ row.title || `ID ${row.item.tmdb_id}` }}</strong>
          <p class="hint">{{ mediaKind(row.item.type) }} · ID {{ row.item.tmdb_id }}</p>
          <p v-if="row.outcome === 'not_found'" class="hint">aucun {{ mediaKind(row.item.type) }} TMDB avec cet ID</p>
        </div>
        <span class="offline-sync-pill" :class="row.outcome">{{ resultBadge(row) }}</span>
        <div class="offline-sync-actions">
          <router-link v-if="row.mediaId" class="link-button" :to="`/medias/${row.mediaId}`">Fiche</router-link>
          <template v-if="row.outcome === 'not_found'">
            <button type="button" class="link-button" @click="tryAlternate(row)">
              Essayer en {{ mediaKind(alternateFollowType(row.item.type)).toLowerCase() }}
            </button>
            <button type="button" class="link-button danger-text" @click="removeResultRow(row)">Retirer</button>
          </template>
        </div>
      </li>
    </ul>
    <p class="hint">
      Les éléments ajoutés ou déjà suivis sont retirés de la file. L'ID introuvable reste dans la file.
    </p>
    <button type="button" class="link-button" @click="clearSyncResults">Retour à la file</button>
  </section>

  <section v-else-if="showQueueOnly || queueCount > 0" class="stack offline-follow">
    <template v-if="mode === 'server_down' && route.query.file !== '1'">
      <h1>Serveur injoignable</h1>
      <p class="hint">
        MediaTracker ne répond pas. Notez l'ID TMDB d'un film ou d'une série : il est gardé sur cet appareil et
        envoyé dès que le serveur répond.
      </p>
    </template>
    <template v-else>
      <h1>File en attente</h1>
      <p class="hint">{{ queueSubtitle }}</p>
    </template>

    <p v-if="duplicate" class="warn-banner">
      Déjà dans la file : ID {{ duplicate.tmdb_id }} · {{ mediaKind(duplicate.type) }}, ajouté à
      {{ formatParisClock(duplicate.added_at) }}. Rien n'a été ajouté.
    </p>
    <p v-if="formError" class="error">{{ formError }}</p>
    <p v-if="pendingDelete" class="offline-undo">
      Suppression de {{ pendingDelete.label }}…
      <button type="button" class="link-button" @click="undoDelete">Annuler</button>
    </p>

    <form class="panel offline-qform" @submit.prevent="submitAdd">
      <h2>Ajouter un ID</h2>
      <label>
        <span>ID TMDB</span>
        <input v-model="input" class="input-big" inputmode="numeric" autocomplete="off" />
      </label>
      <p class="hint">Chiffres seulement, ou collez l'adresse de la page TMDB : l'ID et le type sont repris.</p>
      <div class="offline-type-toggle" role="group" aria-label="Type">
        <span>Type</span>
        <div class="segment-toggle">
          <button type="button" :aria-pressed="kind === 'film'" @click="kind = 'film'">Film</button>
          <button type="button" :aria-pressed="kind === 'series'" @click="kind = 'series'">Série</button>
        </div>
      </div>
      <p class="actions">
        <button type="submit" class="primary">Ajouter à la file</button>
      </p>
    </form>

    <div v-if="items.length" class="offline-queue">
      <ul>
        <li
          v-for="item in items"
          :key="item.id"
          class="offline-qrow"
          :class="{ dup: highlightId === item.id }"
        >
          <div>
            <strong>ID {{ item.tmdb_id }}</strong>
            <p class="hint">Titre affiché après l'envoi</p>
            <p class="hint">{{ addedLabel(item) }}</p>
          </div>
          <span class="offline-type-pill">{{ mediaKind(item.type) }}</span>
          <button type="button" class="offline-del" aria-label="Retirer" @click="scheduleDelete(item)">🗑</button>
        </li>
      </ul>
      <div class="offline-qsend">
        <button type="button" class="link-button" :disabled="busy" @click="sendNow">Envoyer maintenant</button>
        <p class="hint small">{{ lastTryLabel }}</p>
        <button type="button" class="link-button" @click="retryConnection">Réessayer la connexion</button>
      </div>
    </div>
  </section>
</template>

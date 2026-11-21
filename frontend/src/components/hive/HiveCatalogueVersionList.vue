<script setup lang="ts">
import { ref } from "vue";
import { RouterLink } from "vue-router";

import { useHiveCatalogueRequestRow } from "../../composables/useHiveCatalogueRequestRow";
import { formatBytes } from "../../diskFormat";
import { hiveVersionProfileCompatible, hiveVersionRecommended } from "../../hive/hiveVersionProfile";

export interface HiveCatalogueVersionRow {
  version_key: string;
  disponible: boolean;
  source_count: number;
  quality?: string | null;
  language?: string | null;
  size_bytes?: number | null;
  ruche_count?: number | null;
  recommended?: boolean;
  profile_compatible?: boolean;
}

const props = defineProps<{
  workKey: string;
  versions: HiveCatalogueVersionRow[];
  season?: number | null;
  episode?: number | null;
  busy?: boolean;
  compact?: boolean;
  /** Série agrégée : ne lance pas le POST, seulement sélectionne une version. */
  pickOnly?: boolean;
  selectedVersionKey?: string | null;
}>();

const emit = defineEmits<{ requested: []; pick: [versionKey: string] }>();

const { stateFor, errorFor, postRequest } = useHiveCatalogueRequestRow();
const localBusy = ref(false);
const confirmKey = ref<string | null>(null);

function compatible(ver: HiveCatalogueVersionRow): boolean {
  return hiveVersionProfileCompatible(ver);
}

function versionLabel(ver: HiveCatalogueVersionRow): string {
  const q = (ver.quality || ver.version_key.split("+", 1)[0] || "").trim();
  const parts = ver.version_key.split("+");
  const lang = (ver.language || (parts.length > 1 ? parts[1] : "") || "").trim();
  if (q && lang) {
    return `${q} · ${lang}`;
  }
  return ver.version_key;
}

function rowKey(ver: HiveCatalogueVersionRow): string {
  return ver.version_key;
}

async function submit(ver: HiveCatalogueVersionRow) {
  if (!ver.disponible) {
    return;
  }
  if (!compatible(ver) && confirmKey.value !== ver.version_key) {
    confirmKey.value = ver.version_key;
    return;
  }
  confirmKey.value = null;
  if (props.pickOnly) {
    emit("pick", ver.version_key);
    return;
  }
  localBusy.value = true;
  try {
    const body: { version_key: string; season?: number; episode?: number } = {
      version_key: ver.version_key,
    };
    if (props.season != null) {
      body.season = props.season;
    }
    if (props.episode != null) {
      body.episode = props.episode;
    }
    const ok = await postRequest(rowKey(ver), props.workKey, body);
    if (ok) {
      emit("requested");
    }
  } finally {
    localBusy.value = false;
  }
}

const disabled = () => Boolean(props.busy || localBusy.value);

function buttonLabel(ver: HiveCatalogueVersionRow): string {
  const state = stateFor(rowKey(ver));
  if (state === "sending") {
    return "Envoi…";
  }
  return "Télécharger";
}
</script>

<template>
  <ul class="hive-version-list" :class="{ compact }" data-testid="hive-catalogue-version-list">
    <li
      v-for="ver in versions"
      :key="ver.version_key"
      class="hive-version-row card"
      :class="{ selected: pickOnly && selectedVersionKey === ver.version_key }"
    >
      <div class="hive-version-head">
        <strong>{{ versionLabel(ver) }}</strong>
        <span v-if="hiveVersionRecommended(ver)" class="pill ok">Conseillée</span>
        <span v-else-if="compatible(ver)" class="pill neutral">Compatible</span>
        <span v-else class="pill warn">Hors profil</span>
      </div>
      <p class="hint hive-version-meta">
        {{ ver.source_count }} source{{ ver.source_count > 1 ? "s" : "" }}
        <span v-if="ver.size_bytes"> · {{ formatBytes(ver.size_bytes) }}</span>
        <span v-if="!ver.disponible"> · Indisponible pour l’instant</span>
      </p>
      <p v-if="confirmKey === ver.version_key" class="hint hive-version-confirm" data-testid="hive-version-confirm">
        Télécharger quand même ?
      </p>
      <template v-if="stateFor(rowKey(ver)) === 'requested'">
        <p class="ok hive-version-requested" data-testid="hive-version-requested">
          <RouterLink to="/telechargements">Demandé · voir dans Téléchargements</RouterLink>
        </p>
        <p class="hint hive-version-sent" data-testid="hive-version-sent">Demande envoyée.</p>
      </template>
      <div v-else class="hive-version-actions row wrap">
        <button
          v-if="compatible(ver)"
          type="button"
          class="btn btn-sm primary"
          :disabled="disabled() || !ver.disponible || stateFor(rowKey(ver)) === 'sending'"
          :data-testid="`hive-version-download-${ver.version_key}`"
          @click="submit(ver)"
        >
          {{ buttonLabel(ver) }}
        </button>
        <template v-else>
          <button
            v-if="confirmKey !== ver.version_key"
            type="button"
            class="btn btn-sm"
            :disabled="disabled() || !ver.disponible || stateFor(rowKey(ver)) === 'sending'"
            @click="submit(ver)"
          >
            {{ buttonLabel(ver) }}
          </button>
          <button
            v-else
            type="button"
            class="btn btn-sm primary"
            :disabled="disabled() || !ver.disponible"
            data-testid="hive-version-confirm-btn"
            @click="submit(ver)"
          >
            Confirmer
          </button>
          <button
            v-if="confirmKey === ver.version_key"
            type="button"
            class="btn btn-sm btn-ghost"
            :disabled="disabled()"
            @click="confirmKey = null"
          >
            Annuler
          </button>
        </template>
      </div>
      <p
        v-if="errorFor(rowKey(ver))"
        class="error hive-version-row-error"
        role="alert"
        :data-testid="`hive-version-error-${ver.version_key}`"
      >
        {{ errorFor(rowKey(ver)) }}
      </p>
    </li>
  </ul>
</template>

<style scoped>
.hive-version-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 0.65rem;
}
.hive-version-row {
  padding: 0.75rem;
  display: grid;
  gap: 0.35rem;
}
.hive-version-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.5rem;
}
.hive-version-actions {
  gap: 0.5rem;
}
.hive-version-requested a {
  font-weight: 600;
}
.hive-version-row-error {
  margin: 0;
  font-size: 0.9rem;
}
.pill.warn {
  background: color-mix(in srgb, var(--warn, #c9a227) 22%, transparent);
  color: inherit;
}
.pill.neutral {
  background: color-mix(in srgb, var(--muted, #888) 18%, transparent);
  color: inherit;
}
.hive-version-list.compact .hive-version-row {
  padding: 0.65rem;
}
.hive-version-row.selected {
  outline: 2px solid color-mix(in srgb, var(--accent) 55%, transparent);
}
</style>
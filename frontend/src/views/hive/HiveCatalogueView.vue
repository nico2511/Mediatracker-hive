<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import PosterCard from "../../components/PosterCard.vue";
import StatusIcon from "../../components/StatusIcon.vue";
import HiveContextMenuItems from "../../components/HiveContextMenuItems.vue";
import { useHiveEnabled } from "../../composables/useHive";
import { api, userFacingApiError } from "../../api";
import {
  hiveCatalogueShowProgressBar,
  hiveCatalogueStateLabel,
  type HiveCatalogueCardState,
} from "../../hive/hiveCatalogueCard";
import { mediaKind } from "../../labels";

interface VersionRow {
  version_key: string;
  disponible: boolean;
  source_count: number;
}

interface WorkRow extends HiveCatalogueCardState {
  work_key: string;
  type: string | null;
  title: string;
  year: number | null;
  poster_url: string | null;
  disponible: boolean;
  in_library: boolean;
  season: number | null;
  episode: number | null;
  media_id: number | null;
  media_item_id: number | null;
  versions: VersionRow[];
  episode_count: number | null;
}

const router = useRouter();
const { hiveEnabled } = useHiveEnabled();
const items = ref<WorkRow[]>([]);
const refreshedAt = ref<string | null>(null);
const error = ref("");
const loading = ref(false);
const query = ref("");
const typeFilter = ref<"" | "film" | "series">("");
const hideOwned = ref(false);

const subtitle = (row: WorkRow) => {
  if (row.type === "series") {
    if (row.season != null && row.episode != null) {
      return `S${row.season} · E${String(row.episode).padStart(2, "0")}`;
    }
    if (row.episode_count != null && row.episode_count > 0) {
      return `${row.episode_count} épisode${row.episode_count > 1 ? "s" : ""} catalogue`;
    }
  }
  if (row.year) {
    return String(row.year);
  }
  return "";
};

const versionLabel = (row: WorkRow) => {
  const count = row.versions.length;
  if (!count) {
    return "";
  }
  const src = row.versions.reduce((sum, v) => sum + v.source_count, 0);
  return `${count} version${count > 1 ? "s" : ""} · ${src} source${src > 1 ? "s" : ""}`;
};

const stateLine = (row: WorkRow) => hiveCatalogueStateLabel(row, true);

function showProgress(row: WorkRow): boolean {
  return hiveCatalogueShowProgressBar(row.card_state, row.progress_pct);
}

function progressWidth(row: WorkRow): string {
  const pct = row.progress_pct ?? 0;
  return `${Math.min(100, Math.max(0, pct))}%`;
}

function isSeriesAggregate(row: WorkRow): boolean {
  return row.type === "series" && row.season == null && row.episode == null;
}

function canRequest(row: WorkRow): boolean {
  if (isSeriesAggregate(row)) {
    return false;
  }
  return row.card_state === "available";
}

function showOpenFiche(row: WorkRow): boolean {
  return isSeriesAggregate(row) && row.card_state === "available";
}

function showFollow(row: WorkRow): boolean {
  return !row.followed && (row.card_state === "unavailable" || row.card_state === "waiting");
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams();
    if (query.value.trim()) {
      params.set("q", query.value.trim());
    }
    if (typeFilter.value) {
      params.set("media_type", typeFilter.value);
    }
    if (hideOwned.value) {
      params.set("hide_owned", "true");
    }
    params.set("refresh", "true");
    const page = await api<{ items: WorkRow[]; refreshed_at: string | null }>(
      `/api/v1/hive/catalogue?${params.toString()}`,
    );
    items.value = page.items;
    refreshedAt.value = page.refreshed_at;
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger le catalogue Hive.");
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const empty = computed(() => !loading.value && !items.value.length);

function openWork(row: WorkRow) {
  void router.push(`/hive/catalogue/${row.work_key}`);
}

async function menuFollow(row: WorkRow) {
  try {
    await api(`/api/v1/hive/catalogue/${row.work_key}/follow`, { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de suivre cette œuvre.");
  }
}

async function menuRequest(row: WorkRow) {
  try {
    const body =
      row.type === "series" && row.season != null && row.episode != null
        ? JSON.stringify({ season: row.season, episode: row.episode })
        : "{}";
    await api(`/api/v1/hive/catalogue/${row.work_key}/request`, { method: "POST", body });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Transfert Hive indisponible pour le moment.");
  }
}
</script>

<template>
  <div class="hive-catalogue">
    <div class="toolbar row sp wrap">
      <input v-model="query" class="input" type="search" placeholder="Rechercher…" @change="load" />
      <div class="chips">
        <button type="button" class="chip" :class="{ on: typeFilter === '' }" @click="typeFilter = ''; load()">
          Tout
        </button>
        <button
          type="button"
          class="chip"
          :class="{ on: typeFilter === 'film' }"
          @click="typeFilter = 'film'; load()"
        >
          Films
        </button>
        <button
          type="button"
          class="chip"
          :class="{ on: typeFilter === 'series' }"
          @click="typeFilter = 'series'; load()"
        >
          Séries
        </button>
      </div>
      <label class="row">
        <input v-model="hideOwned" type="checkbox" @change="load" />
        <span class="hint">Cacher ce que j’ai déjà</span>
      </label>
    </div>
    <p v-if="refreshedAt" class="hint hive-catalogue-refreshed">Catalogue fusionné actualisé.</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="loading" class="hint">Chargement…</p>
    <p v-else-if="empty" class="hint">Aucune œuvre dans le catalogue fusionné pour l’instant.</p>
    <details v-if="!loading && !empty" class="hive-catalogue-legend">
      <summary class="linkish">Comprendre les états</summary>
      <p class="hint">
        Une seule pastille par carte : erreur, transfert, préparation, bibliothèque, suivi en attente, disponible ou
        indisponible. Le détail des épisodes d’une série est dans la fiche Hive.
      </p>
    </details>
    <div v-if="!loading && !empty" class="poster-grid pc-grid hive-catalogue-grid">
      <PosterCard
        v-for="row in items"
        :key="row.work_key"
        :title="row.title"
        :kind-label="mediaKind(row.type || 'film')"
        :poster-url="row.poster_url"
        :fiche-to="`/hive/catalogue/${row.work_key}`"
        hide-more-button
        @open="openWork(row)"
      >
        <template #overlay>
          <span
            class="media-library-status hive-card-state"
            :class="`tone-${row.tone}`"
            data-testid="hive-card-state"
          >
            <span class="media-library-status-dot" aria-hidden="true" />
            {{ stateLine(row) }}
          </span>
          <div v-if="showProgress(row)" class="hive-card-progress" aria-hidden="true">
            <i :style="{ width: progressWidth(row) }" />
          </div>
        </template>
        <template #meta>
          <span class="hint media-library-kind">{{ mediaKind(row.type || "film") }}</span>
          <strong>{{ row.title }}</strong>
          <span v-if="subtitle(row) || versionLabel(row)" class="hint media-library-year">
            <template v-if="subtitle(row)">{{ subtitle(row) }}</template>
            <template v-if="subtitle(row) && versionLabel(row)"> · </template>
            <template v-if="versionLabel(row)">{{ versionLabel(row) }}</template>
          </span>
          <span class="hint icon-line pc-card-icons">
            <StatusIcon v-if="row.shared_by_me" name="followed" label="Partagé par moi" />
            <StatusIcon v-if="row.followed" name="eye" label="Suivi" />
            <StatusIcon v-if="row.on_disk" name="disk" label="Sur le disque" />
          </span>
          <p v-if="row.error_line" class="pc-line danger">{{ row.error_line }}</p>
        </template>
        <template #actions>
          <button v-if="showFollow(row)" type="button" @click="menuFollow(row)">Suivre</button>
          <button
            v-if="showOpenFiche(row)"
            type="button"
            data-testid="hive-card-open-fiche"
            @click="openWork(row)"
          >
            Ouvrir la fiche
          </button>
          <button
            v-else-if="canRequest(row)"
            type="button"
            data-testid="hive-card-request"
            @click="menuRequest(row)"
          >
            Télécharger
          </button>
          <HiveContextMenuItems
            v-if="row.media_item_id"
            :media-item-id="row.media_item_id"
            :media-id="row.media_id"
            :hive-enabled="hiveEnabled"
            :versions-to="`/hive/catalogue/${row.work_key}`"
            @changed="load"
          />
        </template>
      </PosterCard>
    </div>
  </div>
</template>

<style scoped>
.hive-tag {
  display: inline-flex;
  font-size: 0.68rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 4px;
  margin: 2px;
  background: rgba(10, 11, 12, 0.78);
}
.tone-danger {
  color: var(--danger);
}
.tone-honey {
  color: var(--honey, #d9b46a);
}
.tone-muted {
  color: var(--muted);
}
.tone-info {
  color: var(--info);
}
.tone-warn {
  color: var(--warn);
}
.tone-ok {
  color: var(--ok);
}
.pc-line.honey {
  color: var(--honey, #d9b46a);
  font-size: 0.76rem;
}
.pc-line.danger {
  color: var(--danger);
  font-size: 0.76rem;
}
.hive-card-progress {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 4px;
  background: rgba(0, 0, 0, 0.45);
  z-index: 5;
}
.hive-card-progress > i {
  display: block;
  height: 100%;
  background: var(--honey, #d9b46a);
}
.hive-catalogue-refreshed {
  margin-top: -0.25rem;
}
.hive-card-state {
  position: absolute;
  top: 6px;
  left: 6px;
  z-index: 4;
  max-width: calc(100% - 10px);
}
.hive-catalogue-legend {
  margin: 0.35rem 0 0.75rem;
}
</style>

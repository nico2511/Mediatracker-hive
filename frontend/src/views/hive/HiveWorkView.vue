<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, userFacingApiError } from "../../api";
import ActionMenuPopover from "../../components/ActionMenuPopover.vue";
import HiveBulkShareMenuItems from "../../components/HiveBulkShareMenuItems.vue";
import HiveEmpreintesPanel from "../../components/HiveEmpreintesPanel.vue";
import { useHiveEnabled } from "../../composables/useHive";
import TmdbPosterImg from "../../components/TmdbPosterImg.vue";
import StatusIcon from "../../components/StatusIcon.vue";
import HiveCatalogueVersionList from "../../components/hive/HiveCatalogueVersionList.vue";
import {
  hiveCatalogueShowProgressBar,
  hiveCatalogueStateLabel,
  type HiveCatalogueCardState,
} from "../../hive/hiveCatalogueCard";
import { mediaKind } from "../../labels";
import { useHiveOutOfProfileConfirm } from "../../composables/useHiveOutOfProfileConfirm";
import { hiveVersionProfileCompatible } from "../../hive/hiveVersionProfile";

interface VersionRow {
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

interface WorkRow extends HiveCatalogueCardState {
  work_key: string;
  title: string;
  type: string | null;
  year: number | null;
  poster_url: string | null;
  overview: string | null;
  disponible: boolean;
  in_library: boolean;
  season: number | null;
  episode: number | null;
  versions: VersionRow[];
  media_id: number | null;
  media_item_id: number | null;
  episode_count: number | null;
  episodes: WorkRow[] | null;
}

const route = useRoute();
const router = useRouter();
const work = ref<WorkRow | null>(null);
const error = ref("");
const busy = ref(false);
const compactLabels = ref(false);
const selectedEpisodeVersionKey = ref<string | null>(null);
/** Série : sélection hors profil déjà confirmée dans la liste des versions (évite double confirmation). */
const outOfProfileVersionPickConfirmed = ref(false);
const { pendingId: confirmTargetId, resetConfirm, beginOrConfirm, needsConfirm } = useHiveOutOfProfileConfirm();
const HERO_REQUEST_ID = "hero";
const { hiveEnabled } = useHiveEnabled();
const hiveEmpreintesSignal = ref(0);

let mediaQuery: MediaQueryList | null = null;

function syncCompactLabels() {
  compactLabels.value = Boolean(mediaQuery?.matches);
}

const isSeriesShell = computed(
  () => work.value?.type === "series" && work.value.season == null && work.value.episode == null,
);

const stateLabelShort = (row: WorkRow) => hiveCatalogueStateLabel(row, compactLabels.value);

const heroStateLabel = computed(() => (work.value ? stateLabelShort(work.value) : ""));

function showProgress(row: WorkRow): boolean {
  return hiveCatalogueShowProgressBar(row.card_state, row.progress_pct);
}

function episodeSlot(row: WorkRow): string {
  if (row.season == null || row.episode == null) {
    return row.title;
  }
  return `S${row.season} · E${String(row.episode).padStart(2, "0")}`;
}

function pickDefaultVersionSelection() {
  if (!work.value?.versions.length) {
    selectedEpisodeVersionKey.value = null;
    return;
  }
  const recommended = work.value.versions.find((ver) => ver.recommended && ver.disponible);
  const compatible = work.value.versions.find((ver) => ver.profile_compatible && ver.disponible);
  const fallback = work.value.versions.find((ver) => ver.disponible) ?? work.value.versions[0];
  selectedEpisodeVersionKey.value = (recommended ?? compatible ?? fallback)?.version_key ?? null;
  outOfProfileVersionPickConfirmed.value = false;
}

function onVersionPicked(versionKey: string) {
  selectedEpisodeVersionKey.value = versionKey;
  const ver = work.value?.versions.find((row) => row.version_key === versionKey);
  outOfProfileVersionPickConfirmed.value = Boolean(
    ver && !hiveVersionProfileCompatible(ver),
  );
}

const selectedVersion = computed(() => {
  const key = selectedEpisodeVersionKey.value;
  if (!key || !work.value) {
    return null;
  }
  return work.value.versions.find((ver) => ver.version_key === key) ?? null;
});

async function load() {
  const key = route.params.workKey;
  if (typeof key !== "string") {
    return;
  }
  error.value = "";
  try {
    work.value = await api<WorkRow>(`/api/v1/hive/catalogue/${encodeURIComponent(key)}`);
    pickDefaultVersionSelection();
  } catch (err) {
    work.value = null;
    error.value = userFacingApiError(err, "Fiche Hive introuvable.");
  }
}

onMounted(() => {
  mediaQuery = window.matchMedia("(hover: none), (max-width: 720px)");
  syncCompactLabels();
  mediaQuery.addEventListener("change", syncCompactLabels);
  void load();
});

onUnmounted(() => {
  mediaQuery?.removeEventListener("change", syncCompactLabels);
});

watch(() => route.params.workKey, () => void load());

watch(selectedEpisodeVersionKey, () => resetConfirm());

async function follow() {
  if (!work.value) {
    return;
  }
  busy.value = true;
  try {
    await api(`/api/v1/hive/catalogue/${work.value.work_key}/follow`, { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de suivre.");
  } finally {
    busy.value = false;
  }
}

async function requestTarget(target: WorkRow, version?: VersionRow) {
  busy.value = true;
  error.value = "";
  try {
    const body: { version_key?: string; season?: number; episode?: number } = {};
    const vk = version?.version_key;
    if (vk) {
      body.version_key = vk;
    }
    if (target.season != null) {
      body.season = target.season;
    }
    if (target.episode != null) {
      body.episode = target.episode;
    }
    await api(`/api/v1/hive/catalogue/${target.work_key}/request`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Transfert indisponible.");
  } finally {
    busy.value = false;
  }
}

function requestEpisodeFromSelection(ep: WorkRow) {
  const ver = selectedVersion.value;
  if (!ver) {
    error.value = "Choisissez une version dans la liste.";
    return;
  }
  const run = () => void requestTarget(ep, ver);
  if (isSeriesShell.value && outOfProfileVersionPickConfirmed.value) {
    void run();
    return;
  }
  beginOrConfirm(ep.work_key, ver, run);
}

function requestFilmAuto() {
  if (!work.value) {
    return;
  }
  resetConfirm();
  void requestTarget(work.value);
}

function onHeroHiveDownload() {
  if (!work.value) {
    return;
  }
  const ver = selectedVersion.value;
  if (ver && !hiveVersionProfileCompatible(ver)) {
    beginOrConfirm(HERO_REQUEST_ID, ver, () => void requestTarget(work.value!, ver));
    return;
  }
  requestFilmAuto();
}

function heroNeedsConfirm(): boolean {
  return needsConfirm(HERO_REQUEST_ID, selectedVersion.value);
}

function episodeNeedsConfirm(ep: WorkRow): boolean {
  return needsConfirm(ep.work_key, selectedVersion.value);
}

function canRequest(row: WorkRow): boolean {
  return row.card_state === "available";
}

const showHeroFollow = computed(() => Boolean(work.value && !work.value.followed));
const showHeroRequest = computed(
  () => Boolean(work.value && canRequest(work.value) && !isSeriesShell.value),
);
const showHeroMediaLink = computed(() => Boolean(work.value?.media_id));
const showHeroHiveShare = computed(() => Boolean(hiveEnabled.value && work.value?.media_id && work.value.in_library));
const showHeroMenu = computed(
  () => showHeroFollow.value || showHeroRequest.value || showHeroMediaLink.value || showHeroHiveShare.value,
);

</script>

<template>
  <section v-if="work" class="stack hive-work">
    <button type="button" class="btn btn-ghost" @click="router.back()">← Retour</button>
    <article class="hero panel fiche-hero" data-testid="hive-work-hero">
      <TmdbPosterImg
        v-if="work.poster_url"
        class="poster fiche-poster"
        :url="work.poster_url"
        :alt="work.title"
        thumb
      />
      <div v-else class="poster fiche-poster fiche-poster-placeholder">Affiche</div>
      <div class="fiche-hero-body">
        <div class="fiche-hero-id">
          <h1>
            {{ work.title }}<template v-if="work.year"> ({{ work.year }})</template>
          </h1>
          <p class="meta hint">
            {{ mediaKind(work.type || "film") }}
            <template v-if="isSeriesShell && work.episode_count">
              · {{ work.episode_count }} épisode{{ work.episode_count > 1 ? "s" : "" }} catalogue
            </template>
            <template v-else-if="work.season != null && work.episode != null">
              · {{ episodeSlot(work) }}
            </template>
          </p>
          <p class="fiche-hero-badges">
            <span class="pill" :class="work.tone === 'ok' ? 'ok' : work.tone === 'info' ? 'info' : 'warn'">
              <StatusIcon name="file" label="" aria-hidden="true" />
              {{ heroStateLabel }}
            </span>
            <span v-if="work.followed" class="pill ok">
              <StatusIcon name="followed" label="" aria-hidden="true" />
              Suivi
            </span>
            <span v-if="work.shared_by_me" class="pill info">Partagé par moi</span>
            <span v-if="work.on_disk" class="pill info">Sur disque</span>
          </p>
          <p v-if="showProgress(work)" class="progress fiche-progress">
            <span class="bar fiche-progress-bar" aria-hidden="true">
              <i :style="{ width: `${Math.round(work.progress_pct ?? 0)}%` }" />
            </span>
          </p>
          <p v-if="work.error_line" class="error">{{ work.error_line }}</p>
          <p
            v-if="heroNeedsConfirm()"
            class="hint hive-version-confirm"
            data-testid="hive-hero-version-confirm"
          >
            Télécharger quand même ?
          </p>
        </div>
        <div class="fiche-hero-rest">
          <p v-if="work.overview" class="overview hint">{{ work.overview }}</p>
          <div class="actions row wrap fiche-state-actions">
            <ActionMenuPopover
              v-if="showHeroMenu"
              label="Actions"
              trigger-variant="toggle"
              compact-on-phone
              menu-aria-label="Actions fiche Hive"
              data-testid="hive-work-actions-menu"
            >
              <button
                v-if="showHeroFollow"
                type="button"
                class="action-menu-item"
                role="menuitem"
                :disabled="busy"
                data-testid="hive-work-follow"
                @click="follow"
              >
                Suivre
              </button>
              <button
                v-if="showHeroRequest"
                type="button"
                class="action-menu-item"
                role="menuitem"
                :disabled="busy"
                data-testid="hive-work-request"
                @click="onHeroHiveDownload"
              >
                {{ heroNeedsConfirm() ? "Confirmer" : "Télécharger via Hive" }}
              </button>
              <button
                v-if="showHeroRequest && heroNeedsConfirm()"
                type="button"
                class="action-menu-item"
                role="menuitem"
                :disabled="busy"
                data-testid="hive-hero-cancel-confirm"
                @click="resetConfirm"
              >
                Annuler
              </button>
              <HiveBulkShareMenuItems
                v-if="showHeroHiveShare && work.media_id"
                :media-id="work.media_id"
                :hive-enabled="hiveEnabled"
                @changed="load"
                @open-empreintes="hiveEmpreintesSignal += 1"
              />
              <RouterLink
                v-if="showHeroMediaLink"
                class="action-menu-item"
                role="menuitem"
                :to="`/medias/${work.media_id}`"
              >
                Ma fiche Médias
              </RouterLink>
            </ActionMenuPopover>
          </div>
        </div>
      </div>
    </article>

    <HiveEmpreintesPanel
      v-if="showHeroHiveShare && work.media_id"
      :media-id="work.media_id"
      :hive-enabled="hiveEnabled"
      :open-signal="hiveEmpreintesSignal"
    />

    <section v-if="isSeriesShell && work.episodes?.length" class="panel stack" aria-labelledby="hive-episodes-title">
      <h2 id="hive-episodes-title">Épisodes catalogue</h2>
      <ul class="episode-list">
        <li v-for="ep in work.episodes" :key="ep.work_key" class="card episode-row">
          <div class="row sp wrap">
            <div>
              <strong>{{ episodeSlot(ep) }}</strong>
              <p class="hint">{{ stateLabelShort(ep) }}</p>
              <p v-if="ep.error_line" class="error">{{ ep.error_line }}</p>
            </div>
            <div class="row stack episode-actions">
              <p
                v-if="episodeNeedsConfirm(ep)"
                class="hint hive-version-confirm"
                data-testid="hive-episode-version-confirm"
              >
                Télécharger quand même ?
              </p>
              <div class="row">
                <button
                  v-if="canRequest(ep)"
                  type="button"
                  class="btn btn-sm primary"
                  :disabled="busy || !selectedEpisodeVersionKey"
                  data-testid="hive-episode-request"
                  @click="requestEpisodeFromSelection(ep)"
                >
                  {{ episodeNeedsConfirm(ep) ? "Confirmer" : "Télécharger" }}
                </button>
                <button
                  v-if="episodeNeedsConfirm(ep)"
                  type="button"
                  class="btn btn-sm btn-ghost"
                  :disabled="busy"
                  data-testid="hive-episode-cancel-confirm"
                  @click="resetConfirm"
                >
                  Annuler
                </button>
              </div>
            </div>
          </div>
        </li>
      </ul>
    </section>

    <section class="panel stack" aria-labelledby="hive-versions-title">
      <h2 id="hive-versions-title">Versions</h2>
      <HiveCatalogueVersionList
        v-if="work.versions.length"
        :work-key="work.work_key"
        :versions="work.versions"
        :season="work.season"
        :episode="work.episode"
        :busy="busy"
        :pick-only="isSeriesShell"
        :selected-version-key="selectedEpisodeVersionKey"
        @pick="onVersionPicked"
        @requested="load"
      />
      <p v-else class="hint">Aucune version annoncée pour le moment.</p>
      <p v-if="isSeriesShell" class="hint">Choisissez une version, puis un épisode pour lancer le transfert.</p>
    </section>
  </section>
  <p v-else-if="error" class="error" role="alert">{{ error }}</p>
</template>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  border: 0;
}
.off {
  color: var(--muted);
}
.overview {
  max-width: 42rem;
  line-height: 1.45;
}
.version-pickers {
  border: 0;
  padding: 0;
  margin: 0;
  gap: 0.75rem;
}
.version-radio-group {
  display: grid;
  gap: 0.35rem;
}
.version-radios {
  gap: 0.5rem 1rem;
}
.version-radio {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  cursor: pointer;
}
.version-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.35rem 0.5rem;
}
.episode-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  gap: 0.75rem;
}
.episode-row {
  padding: 0.75rem;
}
</style>

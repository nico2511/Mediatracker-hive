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
import { formatBytes } from "../../diskFormat";
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
  quality?: string | null;
  language?: string | null;
  size_bytes?: number | null;
  ruche_count?: number | null;
  recommended?: boolean;
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
const selectedQuality = ref<string | null>(null);
const selectedLanguage = ref<string | null>(null);
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

const qualityOptions = computed(() => {
  const set = new Set<string>();
  for (const ver of work.value?.versions ?? []) {
    const q = (ver.quality || ver.version_key.split("+", 1)[0] || "").trim();
    if (q) {
      set.add(q);
    }
  }
  return [...set].sort((a, b) => a.localeCompare(b, "fr"));
});

const languageOptions = computed(() => {
  const set = new Set<string>();
  for (const ver of work.value?.versions ?? []) {
    const parts = ver.version_key.split("+");
    const lang = (ver.language || (parts.length > 1 ? parts[1] : "") || "").trim();
    if (lang) {
      set.add(lang);
    }
  }
  return [...set].sort((a, b) => a.localeCompare(b, "fr"));
});

function versionForSelection(quality: string, language: string): VersionRow | undefined {
  return work.value?.versions.find((ver) => {
    const q = (ver.quality || ver.version_key.split("+", 1)[0] || "").trim();
    const parts = ver.version_key.split("+");
    const lang = (ver.language || (parts.length > 1 ? parts[1] : "") || "").trim();
    return q === quality && lang === language;
  });
}

const selectedVersion = computed(() => {
  const q = selectedQuality.value;
  const lang = selectedLanguage.value;
  if (!q || !lang) {
    return null;
  }
  return versionForSelection(q, lang) ?? null;
});

const requestCtaLabel = computed(() => {
  const q = selectedQuality.value;
  const lang = selectedLanguage.value;
  if (!q || !lang) {
    return "Télécharger via Hive";
  }
  return `Télécharger via Hive · ${q} · ${lang}`;
});

function pickDefaultVersionSelection() {
  if (!work.value?.versions.length) {
    selectedQuality.value = null;
    selectedLanguage.value = null;
    return;
  }
  const recommended = work.value.versions.find((ver) => ver.recommended && ver.disponible);
  const fallback = work.value.versions.find((ver) => ver.disponible) ?? work.value.versions[0];
  const seed = recommended ?? fallback;
  const parts = seed.version_key.split("+");
  selectedQuality.value = (seed.quality || parts[0] || "").trim() || null;
  selectedLanguage.value = (seed.language || (parts.length > 1 ? parts[1] : "") || "").trim() || null;
}

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

function requestFilmWithSelection() {
  if (!work.value || !selectedVersion.value) {
    return;
  }
  void requestTarget(work.value, selectedVersion.value);
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

watch([selectedQuality, selectedLanguage], () => {
  if (!selectedQuality.value || !languageOptions.value.includes(selectedLanguage.value || "")) {
    const match = languageOptions.value.find((lang) => versionForSelection(selectedQuality.value || "", lang));
    if (match) {
      selectedLanguage.value = match;
    }
  }
});
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
                @click="requestFilmWithSelection"
              >
                {{ requestCtaLabel }}
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
            <div class="row">
              <button
                v-if="canRequest(ep)"
                type="button"
                class="btn btn-sm primary"
                :disabled="busy"
                @click="requestTarget(ep)"
              >
                Télécharger
              </button>
            </div>
          </div>
        </li>
      </ul>
    </section>

    <section class="panel stack" aria-labelledby="hive-versions-title">
      <h2 id="hive-versions-title">Versions</h2>
      <fieldset v-if="!isSeriesShell && work.versions.length" class="version-pickers stack">
        <legend class="sr-only">Qualité et langue</legend>
        <div v-if="qualityOptions.length" class="version-radio-group">
          <p class="lbl hint">Qualité</p>
          <div class="row wrap version-radios" role="radiogroup" aria-label="Qualité">
            <label v-for="q in qualityOptions" :key="q" class="version-radio">
              <input v-model="selectedQuality" type="radio" name="hive-version-quality" :value="q" />
              <span>{{ q }}</span>
            </label>
          </div>
        </div>
        <div v-if="languageOptions.length" class="version-radio-group">
          <p class="lbl hint">Langue</p>
          <div class="row wrap version-radios" role="radiogroup" aria-label="Langue">
            <label v-for="lang in languageOptions" :key="lang" class="version-radio">
              <input v-model="selectedLanguage" type="radio" name="hive-version-language" :value="lang" />
              <span>{{ lang }}</span>
            </label>
          </div>
        </div>
        <p v-if="selectedVersion" class="hint version-meta">
          <span v-if="selectedVersion.recommended" class="pill ok">Conseillée</span>
          <span>{{ selectedVersion.source_count }} source{{ selectedVersion.source_count > 1 ? "s" : "" }}</span>
          <span v-if="selectedVersion.size_bytes"> · {{ formatBytes(selectedVersion.size_bytes) }}</span>
          <span v-if="selectedVersion.ruche_count">
            · {{ selectedVersion.ruche_count }} ruche{{ selectedVersion.ruche_count > 1 ? "s" : "" }}
          </span>
          <span v-if="selectedVersion && !selectedVersion.disponible" class="off"> · Indisponible pour l’instant</span>
        </p>
        <button
          v-if="canRequest(work)"
          type="button"
          class="btn primary"
          :disabled="busy || !selectedVersion?.disponible"
          data-testid="hive-work-version-request"
          @click="requestFilmWithSelection"
        >
          {{ requestCtaLabel }}
        </button>
      </fieldset>
      <p v-else-if="!work.versions.length" class="hint">Aucune version annoncée pour le moment.</p>
      <p v-if="isSeriesShell" class="hint">Choisissez un épisode ci-dessus pour lancer un transfert.</p>
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

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";

import DeleteFicheDialog from "../components/DeleteFicheDialog.vue";
import MediaQuickView from "../components/MediaQuickView.vue";
import PosterCard from "../components/PosterCard.vue";
import StatusIcon from "../components/StatusIcon.vue";
import { api, userFacingApiError } from "../api";
import { useConnectivity } from "../pwa/connectivity";
import { loadMediaListSnapshot, saveMediaListSnapshot } from "../pwa/offlineSnapshot";
import { fetchAllMedia as fetchAllMediaPages } from "../media/fetchAllMedia";
import { formatParis } from "../time";
import { mediaKind } from "../labels";
import { mediaBadge, mediaOnDisk, mediaWaiting } from "../mediaDisplay";
import { useHiveEnabled } from "../composables/useHive";
import { hiveBadgesFromMediaSummary, type HiveMediaSummary } from "../hive/hiveBadges";
import {
  fetchHiveMediaSummaries,
  HIVE_MEDIA_SUMMARY_LIMIT,
  releaseLoadingThenRefreshHive,
} from "../media/hiveMediaSummaries";
import { tmdbBackdropSrc, tmdbPosterSrc } from "../tmdbImages";
import type { QuickTarget } from "../quick";

const DENSITY_STORAGE_KEY = "mediatracker.medias.density.v2";

interface MediaProgress {
  watched: number;
  total: number;
}

interface MediaRow {
  id: number;
  type: string;
  title: string;
  year: number | null;
  poster_url: string | null;
  backdrop_url?: string | null;
  on_hold: boolean;
  followed: boolean;
  follow_origin?: string;
  availability?: string;
  watched?: boolean;
  on_disk?: boolean;
  disk_label?: string | null;
  created_at?: string;
  jellyfin_web_url?: string | null;
  overview?: string | null;
  tmdb_id?: number | null;
  tmdb_url?: string | null;
  catalog_id?: number | null;
  source_url?: string | null;
  progress?: MediaProgress;
  poll: { allowed: boolean; reason: string };
  cleanup_reason?: string | null;
  tmdb_identify_status?: string | null;
}

type Density = "airy" | "dense";
type StateFilter = "all" | "available" | "waiting" | "disk" | "paused" | "identified";
type FollowFilter = "all" | "followed" | "unfollowed";
type WatchFilter = "all" | "watched" | "in_progress" | "unwatched";
type TypeFilter = "all" | "film" | "series";
type OriginFilter = "all" | "user" | "jellyfin_import" | "identification" | "tmdb_pending";

const { offlineShell, serverActionsDisabled } = useConnectivity();
const { hiveEnabled } = useHiveEnabled();
const hiveSummaries = ref<Record<number, HiveMediaSummary>>({});
const cacheNote = ref("");
const quick = ref<QuickTarget | null>(null);

const items = ref<MediaRow[]>([]);
const loading = ref(false);
const loadingCount = ref(0);
const error = ref("");
const note = ref("");
const query = ref("");
const sort = ref<"alpha" | "added">("alpha");
const filter = ref<StateFilter>("all");
const followFilter = ref<FollowFilter>("all");
const watchFilter = ref<WatchFilter>("all");
const typeFilter = ref<TypeFilter>("all");
const originFilter = ref<OriginFilter>("all");
const density = ref<Density>("airy");
const mobileFiltersOpen = ref(false);
const selecting = ref(false);
const selectedIds = ref<number[]>([]);
const deleteOpen = ref(false);
const deleteBusy = ref(false);
let reloadGeneration = 0;
let hiveSummaryGeneration = 0;

function formatCount(count: number, singular: string, plural: string): string {
  return `${count} ${count <= 1 ? singular : plural}`;
}

function formatSeriesCount(count: number): string {
  return formatCount(count, "série", "séries");
}

function formatFilmCount(count: number): string {
  return formatCount(count, "film", "films");
}

function formatTitleCount(count: number): string {
  return formatCount(count, "titre", "titres");
}

function formatFichesDeletedMessage(count: number): string {
  return formatCount(count, "fiche supprimée", "fiches supprimées");
}

function itemOrigin(item: MediaRow): string {
  return item.follow_origin || "user";
}

function matchesWatchFilter(item: MediaRow): boolean {
  const mode = watchFilter.value;
  if (mode === "all") {
    return true;
  }
  if (item.watched) {
    return mode === "watched";
  }
  const progress = item.progress?.watched ?? 0;
  if (mode === "in_progress") {
    return progress > 0;
  }
  if (mode === "unwatched") {
    return progress === 0;
  }
  return true;
}

function matchesOriginFilter(item: MediaRow): boolean {
  const mode = originFilter.value;
  if (mode === "all") {
    return true;
  }
  if (mode === "tmdb_pending") {
    return item.tmdb_identify_status === "pending";
  }
  return itemOrigin(item) === mode;
}

const visible = computed(() => {
  const needle = query.value.trim().toLocaleLowerCase("fr");
  const rows = items.value.filter((item) => {
    if (needle && !item.title.toLocaleLowerCase("fr").includes(needle)) {
      return false;
    }
    if (filter.value === "paused" && !item.on_hold) {
      return false;
    }
    if (filter.value === "available" && item.availability !== "available") {
      return false;
    }
    if (filter.value === "waiting" && !mediaWaiting(item)) {
      return false;
    }
    if (filter.value === "disk" && !mediaOnDisk(item)) {
      return false;
    }
    if (filter.value === "identified" && itemOrigin(item) !== "identification") {
      return false;
    }
    if (followFilter.value === "followed" && !item.followed) {
      return false;
    }
    if (followFilter.value === "unfollowed" && item.followed) {
      return false;
    }
    if (!matchesWatchFilter(item)) {
      return false;
    }
    if (typeFilter.value !== "all" && item.type !== typeFilter.value) {
      return false;
    }
    if (!matchesOriginFilter(item)) {
      return false;
    }
    return true;
  });
  return [...rows].sort((left, right) => {
    if (sort.value === "added") {
      return (right.created_at || "").localeCompare(left.created_at || "");
    }
    return left.title.localeCompare(right.title, "fr");
  });
});

const libraryStats = computed(() => {
  const series = items.value.filter((item) => item.type === "series").length;
  const films = items.value.filter((item) => item.type === "film").length;
  return { total: items.value.length, series, films };
});

const filteredStats = computed(() => {
  const series = visible.value.filter((item) => item.type === "series").length;
  const films = visible.value.filter((item) => item.type === "film").length;
  return { total: visible.value.length, series, films };
});

const filtersActive = computed(() => {
  return (
    Boolean(query.value.trim()) ||
    filter.value !== "all" ||
    followFilter.value !== "all" ||
    watchFilter.value !== "all" ||
    typeFilter.value !== "all" ||
    originFilter.value !== "all"
  );
});

const activeFilterCount = computed(() => {
  let count = 0;
  if (followFilter.value !== "all") {
    count += 1;
  }
  if (watchFilter.value !== "all") {
    count += 1;
  }
  if (typeFilter.value !== "all") {
    count += 1;
  }
  if (originFilter.value !== "all") {
    count += 1;
  }
  return count;
});

const activeFilterChips = computed(() => {
  const chips: { key: string; label: string }[] = [];
  if (followFilter.value === "followed") {
    chips.push({ key: "follow", label: "Suivis" });
  } else if (followFilter.value === "unfollowed") {
    chips.push({ key: "follow", label: "Non suivis" });
  }
  if (watchFilter.value === "watched") {
    chips.push({ key: "watch", label: "Vus" });
  } else if (watchFilter.value === "in_progress") {
    chips.push({ key: "watch", label: "En cours" });
  } else if (watchFilter.value === "unwatched") {
    chips.push({ key: "watch", label: "Non vus" });
  }
  if (typeFilter.value === "series") {
    chips.push({ key: "type", label: "Séries" });
  } else if (typeFilter.value === "film") {
    chips.push({ key: "type", label: "Films" });
  }
  if (originFilter.value === "user") {
    chips.push({ key: "origin", label: "Ajout manuel" });
  } else if (originFilter.value === "jellyfin_import") {
    chips.push({ key: "origin", label: "Import Jellyfin" });
  } else if (originFilter.value === "identification") {
    chips.push({ key: "origin", label: "Identifiés bibliothèque" });
  } else if (originFilter.value === "tmdb_pending") {
    chips.push({ key: "origin", label: "À identifier TMDB" });
  }
  return chips;
});

const featuredHeroStyle = computed((): Record<string, string> | undefined => {
  const item = featured.value;
  if (!item) {
    return undefined;
  }
  const url = tmdbBackdropSrc(item.backdrop_url) ?? tmdbPosterSrc(item.poster_url);
  return url ? { "--hero-backdrop": `url(${url})` } : undefined;
});

const featured = computed(() => {
  if (!visible.value.length) {
    return null;
  }
  const pool = visible.value;
  const inProgress = pool.find((item) => {
    const progress = item.progress;
    return (
      item.type === "series" &&
      progress &&
      progress.total > 0 &&
      progress.watched > 0 &&
      progress.watched < progress.total
    );
  });
  return inProgress || pool[0];
});

function featuredSubtitle(item: MediaRow): string {
  const parts: string[] = [];
  if (item.year) {
    parts.push(String(item.year));
  }
  parts.push(mediaKind(item.type));
  const progress = item.progress;
  if (item.type === "series" && progress && progress.total > 0) {
    parts.push(`${progress.watched} sur ${progress.total} vus`);
  }
  return parts.join(" · ");
}

function mediaTarget(item: MediaRow): QuickTarget {
  return {
    title: item.title,
    year: item.year,
    overview: item.overview,
    posterUrl: item.poster_url,
    mediaType: item.type,
    followed: item.followed,
    tmdbId: item.tmdb_id,
    tmdbUrl: item.tmdb_url,
    mediaId: item.id,
    catalogId: item.catalog_id,
    sourceUrl: item.source_url,
    jellyfinUrl: item.jellyfin_web_url,
  };
}

function readDensity(): Density {
  try {
    const stored = localStorage.getItem(DENSITY_STORAGE_KEY);
    return stored === "dense" ? "dense" : "airy";
  } catch {
    return "airy";
  }
}

function persistDensity(value: Density) {
  try {
    localStorage.setItem(DENSITY_STORAGE_KEY, value);
  } catch {
    /* private mode */
  }
}

function resetAdvancedFilters() {
  followFilter.value = "all";
  watchFilter.value = "all";
  typeFilter.value = "all";
  originFilter.value = "all";
  query.value = "";
  filter.value = "all";
}

function clearFilterChip(key: string) {
  if (key === "follow") {
    followFilter.value = "all";
  } else if (key === "watch") {
    watchFilter.value = "all";
  } else if (key === "type") {
    typeFilter.value = "all";
  } else if (key === "origin") {
    originFilter.value = "all";
  }
}

watch(density, (value) => {
  persistDensity(value);
});

async function refreshHiveSummaries(parentGeneration = reloadGeneration) {
  if (!hiveEnabled.value || !items.value.length) {
    hiveSummaries.value = {};
    return;
  }
  const generation = ++hiveSummaryGeneration;
  const mediaIds = visible.value.slice(0, HIVE_MEDIA_SUMMARY_LIMIT).map((row) => row.id);
  try {
    const next = await fetchHiveMediaSummaries(api, mediaIds);
    if (generation !== hiveSummaryGeneration || parentGeneration !== reloadGeneration) {
      return;
    }
    hiveSummaries.value = next;
  } catch {
    if (generation !== hiveSummaryGeneration || parentGeneration !== reloadGeneration) {
      return;
    }
  }
}

const visibleSummaryKey = computed(() =>
  visible.value
    .slice(0, HIVE_MEDIA_SUMMARY_LIMIT)
    .map((row) => row.id)
    .join(","),
);

watch(visibleSummaryKey, () => {
  if (hiveEnabled.value && items.value.length) {
    void refreshHiveSummaries();
  }
});

function hiveListBadges(mediaId: number) {
  return hiveBadgesFromMediaSummary(hiveSummaries.value[mediaId]);
}

async function reload() {
  const generation = ++reloadGeneration;
  error.value = "";
  loading.value = true;
  loadingCount.value = items.value.length;
  try {
    const all = await fetchAllMediaPages<MediaRow>(
      (path: string) => api(path),
      () => generation !== reloadGeneration,
      (partial) => {
        if (generation !== reloadGeneration) {
          return;
        }
        items.value = partial;
        loadingCount.value = partial.length;
      },
    );
    if (generation !== reloadGeneration) {
      return;
    }
    items.value = all;
    saveMediaListSnapshot({ items: all });
    cacheNote.value = "";
    await releaseLoadingThenRefreshHive(
      (value) => {
        loading.value = value;
      },
      () => refreshHiveSummaries(generation),
    );
    return;
  } catch (err) {
    if (generation !== reloadGeneration) {
      return;
    }
    if (offlineShell.value) {
      const snap = loadMediaListSnapshot<{ items: MediaRow[] }>();
      if (snap) {
        items.value = snap.body.items;
        cacheNote.value = `Affichage de la dernière visite (${formatParis(snap.saved_at)}). Les affiches ne sont pas conservées hors ligne.`;
        return;
      }
    }
    throw err;
  } finally {
    if (generation === reloadGeneration) {
      loading.value = false;
    }
  }
}

async function onQuickViewChanged() {
  try {
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Liste indisponible.");
  }
}

const selectedCount = computed(() => selectedIds.value.length);

const selectedTitlesForDialog = computed(() => {
  const wanted = new Set(selectedIds.value);
  return items.value.filter((row) => wanted.has(row.id)).map((row) => row.title);
});

function isSelected(id: number): boolean {
  return selectedIds.value.includes(id);
}

function toggleSelected(id: number) {
  const next = new Set(selectedIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  selectedIds.value = [...next];
}

function enterSelection() {
  selecting.value = true;
  selectedIds.value = [];
  quick.value = null;
}

function cancelSelection() {
  selecting.value = false;
  selectedIds.value = [];
  deleteOpen.value = false;
}

function selectAllVisible() {
  selectedIds.value = visible.value.map((row) => row.id);
}

function onCardOpen(item: MediaRow) {
  if (selecting.value) {
    toggleSelected(item.id);
    return;
  }
  quick.value = mediaTarget(item);
}

function onBulkEscape(event: KeyboardEvent) {
  if (event.key !== "Escape" || !selecting.value) {
    return;
  }
  if (deleteOpen.value) {
    deleteOpen.value = false;
    return;
  }
  cancelSelection();
}

watch(selecting, (active) => {
  if (active) {
    window.addEventListener("keydown", onBulkEscape);
  } else {
    window.removeEventListener("keydown", onBulkEscape);
  }
});

onUnmounted(() => {
  window.removeEventListener("keydown", onBulkEscape);
});

async function deleteSelectedFiches() {
  const ids = [...selectedIds.value];
  deleteOpen.value = false;
  if (!ids.length) {
    return;
  }
  deleteBusy.value = true;
  error.value = "";
  try {
    const result = await api<{ deleted: number[] }>("/api/v1/media/bulk", {
      method: "DELETE",
      body: JSON.stringify({ ids, trash: false }),
    });
    const removed = new Set(result.deleted);
    items.value = items.value.filter((row) => !removed.has(row.id));
    const failed = ids.length - result.deleted.length;
    if (failed > 0) {
      note.value = `${formatFichesDeletedMessage(result.deleted.length)}, ${failed} en échec.`;
    } else {
      note.value = formatFichesDeletedMessage(result.deleted.length);
    }
    cancelSelection();
    try {
      await reload();
    } catch (err) {
      error.value = userFacingApiError(err, "Liste indisponible.");
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Suppression des fiches impossible.");
  } finally {
    deleteBusy.value = false;
  }
}

async function unfollowIdentified() {
  error.value = "";
  try {
    const body = await api<{ message: string }>("/api/v1/media/unfollow-identified", { method: "POST" });
    items.value = items.value.map((item) =>
      itemOrigin(item) === "identification" ? { ...item, followed: false } : item,
    );
    note.value = body.message;
  } catch (err) {
    error.value = userFacingApiError(err, "Action impossible.");
  }
}

onMounted(async () => {
  density.value = readDensity();
  try {
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Liste indisponible.");
  }
});
</script>

<template>
  <section class="media-library">
    <article
      v-if="featured"
      class="media-library-hero"
      :style="featuredHeroStyle"
    >
      <div class="media-library-hero-copy">
        <p class="hint media-library-hero-kicker">À la une</p>
        <h2 class="media-library-hero-title">{{ featured.title }}</h2>
        <p class="hint">{{ featuredSubtitle(featured) }}</p>
        <p class="media-library-hero-actions">
          <router-link class="button" :to="`/medias/${featured.id}`">Fiche</router-link>
          <a
            v-if="featured.jellyfin_web_url"
            class="button"
            :href="featured.jellyfin_web_url"
            target="_blank"
            rel="noopener noreferrer"
          >Lire sur Jellyfin</a>
        </p>
      </div>
    </article>

    <header class="media-library-header">
      <h1>Médias</h1>
      <p v-if="cacheNote" class="offline-cache-note">{{ cacheNote }}</p>
      <p v-if="loading" class="hint media-library-loading">
        <span class="media-library-loading-spinner" aria-hidden="true"></span>
        Chargement… {{ formatTitleCount(loadingCount) }} reçu{{ loadingCount > 1 ? "s" : "" }}
      </p>
      <p v-if="libraryStats.total" class="hint media-library-stats">
        {{ formatTitleCount(libraryStats.total) }}
        · {{ formatSeriesCount(libraryStats.series) }}
        · {{ formatFilmCount(libraryStats.films) }}
      </p>
      <p v-if="filtersActive && libraryStats.total" class="hint media-library-stats media-library-stats-filtered">
        {{ formatTitleCount(filteredStats.total) }} affiché{{ filteredStats.total > 1 ? "s" : "" }}
        · {{ formatSeriesCount(filteredStats.series) }}
        · {{ formatFilmCount(filteredStats.films) }}
      </p>
    </header>

    <p class="actions media-library-filters">
      <button
        v-if="!selecting && visible.length"
        type="button"
        :disabled="serverActionsDisabled"
        @click="enterSelection"
      >
        Sélectionner
      </button>
      <button type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">Tous</button>
      <button type="button" :class="{ active: filter === 'available' }" @click="filter = 'available'">Disponibles</button>
      <button type="button" :class="{ active: filter === 'waiting' }" @click="filter = 'waiting'">En attente</button>
      <button type="button" :class="{ active: filter === 'disk' }" @click="filter = 'disk'">Sur le disque</button>
      <button type="button" :class="{ active: filter === 'paused' }" @click="filter = 'paused'">En pause</button>
      <button type="button" :class="{ active: filter === 'identified' }" @click="filter = 'identified'">Suivi par Jellyfin</button>
    </p>
    <p v-if="filter === 'identified'" class="actions">
      <button type="button" :disabled="serverActionsDisabled" @click="unfollowIdentified">Ne plus suivre ces fiches</button>
    </p>

    <form class="filters media-library-toolbar media-library-search-row" @submit.prevent>
      <label class="media-library-search">
        <span class="media-library-search-label-desktop">Recherche</span>
        <input
          v-model="query"
          type="search"
          placeholder="Rechercher dans la collection"
          class="media-library-search-input-desktop"
        />
        <input
          v-model="query"
          type="search"
          placeholder="Rechercher un titre"
          class="media-library-search-input-mobile"
        />
      </label>
      <button
        type="button"
        class="media-library-filters-toggle"
        :aria-expanded="mobileFiltersOpen"
        @click="mobileFiltersOpen = !mobileFiltersOpen"
      >
        Filtres
        <span v-if="activeFilterCount" class="media-library-filters-badge">{{ activeFilterCount }}</span>
      </button>
      <label class="media-library-sort">
        <span>Tri</span>
        <select v-model="sort">
          <option value="alpha">Alphabétique</option>
          <option value="added">Récents</option>
        </select>
      </label>
      <div class="media-library-density" role="group" aria-label="Densité">
        <span class="media-library-density-label">Densité</span>
        <button type="button" :class="{ active: density === 'airy' }" @click="density = 'airy'">Aérée</button>
        <button type="button" :class="{ active: density === 'dense' }" @click="density = 'dense'">Dense</button>
      </div>
    </form>

    <p v-if="activeFilterChips.length && !mobileFiltersOpen" class="media-library-active-chips">
      <span v-for="chip in activeFilterChips" :key="chip.key" class="media-library-filter-chip">
        {{ chip.label }}
        <button type="button" :aria-label="`Retirer ${chip.label}`" @click="clearFilterChip(chip.key)">×</button>
      </span>
      <button type="button" class="linkish" @click="resetAdvancedFilters">Réinitialiser</button>
    </p>

    <form
      class="filters media-library-extra-filters"
      :class="{ 'is-open': mobileFiltersOpen }"
      @submit.prevent
    >
      <div class="media-library-extra-filters-desktop">
        <label>
          <span>Suivi</span>
          <select v-model="followFilter">
            <option value="all">Tous</option>
            <option value="followed">Suivis</option>
            <option value="unfollowed">Non suivis</option>
          </select>
        </label>
        <label>
          <span>Vu</span>
          <select v-model="watchFilter">
            <option value="all">Tous</option>
            <option value="watched">Vus</option>
            <option value="in_progress">En cours</option>
            <option value="unwatched">Non vus</option>
          </select>
        </label>
        <label>
          <span>Type</span>
          <select v-model="typeFilter">
            <option value="all">Tous</option>
            <option value="series">Séries</option>
            <option value="film">Films</option>
          </select>
        </label>
        <label>
          <span>Origine</span>
          <select v-model="originFilter">
            <option value="all">Toutes</option>
            <option value="user">Ajout manuel</option>
            <option value="jellyfin_import">Import Jellyfin</option>
            <option value="identification">Identifiés bibliothèque</option>
            <option value="tmdb_pending">À identifier TMDB</option>
          </select>
        </label>
        <button type="button" class="linkish media-library-reset" @click="resetAdvancedFilters">Réinitialiser</button>
      </div>

      <div class="media-library-extra-filters-mobile">
        <div class="media-library-segment">
          <span class="media-library-segment-label">Suivi</span>
          <p class="actions media-library-segment-actions">
            <button type="button" :class="{ active: followFilter === 'all' }" @click="followFilter = 'all'">Tous</button>
            <button type="button" :class="{ active: followFilter === 'followed' }" @click="followFilter = 'followed'">Suivis</button>
            <button type="button" :class="{ active: followFilter === 'unfollowed' }" @click="followFilter = 'unfollowed'">Non suivis</button>
          </p>
        </div>
        <div class="media-library-segment">
          <span class="media-library-segment-label">Vu</span>
          <p class="actions media-library-segment-actions">
            <button type="button" :class="{ active: watchFilter === 'all' }" @click="watchFilter = 'all'">Tous</button>
            <button type="button" :class="{ active: watchFilter === 'watched' }" @click="watchFilter = 'watched'">Vus</button>
            <button type="button" :class="{ active: watchFilter === 'in_progress' }" @click="watchFilter = 'in_progress'">En cours</button>
            <button type="button" :class="{ active: watchFilter === 'unwatched' }" @click="watchFilter = 'unwatched'">Non vus</button>
          </p>
        </div>
        <div class="media-library-segment">
          <span class="media-library-segment-label">Type</span>
          <p class="actions media-library-segment-actions">
            <button type="button" :class="{ active: typeFilter === 'all' }" @click="typeFilter = 'all'">Tous</button>
            <button type="button" :class="{ active: typeFilter === 'series' }" @click="typeFilter = 'series'">Séries</button>
            <button type="button" :class="{ active: typeFilter === 'film' }" @click="typeFilter = 'film'">Films</button>
          </p>
        </div>
        <label>
          <span>Origine</span>
          <select v-model="originFilter">
            <option value="all">Toutes</option>
            <option value="user">Ajout manuel</option>
            <option value="jellyfin_import">Import Jellyfin</option>
            <option value="identification">Identifiés bibliothèque</option>
            <option value="tmdb_pending">À identifier TMDB</option>
          </select>
        </label>
        <label class="media-library-panel-sort">
          <span>Tri</span>
          <select v-model="sort">
            <option value="alpha">Alphabétique</option>
            <option value="added">Récents</option>
          </select>
        </label>
        <div class="media-library-density media-library-panel-density" role="group" aria-label="Densité">
          <span class="media-library-density-label">Densité</span>
          <button type="button" :class="{ active: density === 'airy' }" @click="density = 'airy'">Aérée</button>
          <button type="button" :class="{ active: density === 'dense' }" @click="density = 'dense'">Dense</button>
        </div>
        <button type="button" class="linkish media-library-reset" @click="resetAdvancedFilters">Réinitialiser</button>
        <button
          type="button"
          class="primary media-library-apply-filters"
          @click="mobileFiltersOpen = false"
        >
          Voir les {{ formatTitleCount(filteredStats.total) }}
        </button>
      </div>
    </form>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="note" class="hint">{{ note }}</p>
    <p v-if="!visible.length && filtersActive && !loading" class="hint media-library-empty">
      Aucun titre ne correspond aux filtres.
      <button type="button" class="linkish" @click="resetAdvancedFilters">Réinitialiser</button>
    </p>
    <p v-else-if="!visible.length && !loading" class="hint">
      Aucun suivi. <router-link to="/suivre">Suivre un titre</router-link>.
    </p>

    <div
      v-if="selecting && visible.length"
      class="media-library-bulk-bar media-library-bulk-bar--top actions"
      role="toolbar"
      aria-label="Sélection des fiches"
    >
      <span class="hint">{{ selectedCount }} sélectionnée{{ selectedCount > 1 ? "s" : "" }}</span>
      <button type="button" :disabled="!visible.length" @click="selectAllVisible">Tout sélectionner</button>
      <button type="button" @click="cancelSelection">Annuler</button>
      <button
        type="button"
        class="danger"
        :disabled="!selectedCount || deleteBusy || serverActionsDisabled"
        @click="deleteOpen = true"
      >
        Supprimer les fiches
      </button>
    </div>

    <div
      v-if="visible.length"
      class="poster-grid pc-grid media-library-grid"
      :class="{ 'pc-grid--dense': density === 'dense', 'media-library-grid--selecting': selecting }"
    >
      <PosterCard
        v-for="item in visible"
        :key="item.id"
        class="media-library-card"
        :class="{ 'is-selected': selecting && isSelected(item.id) }"
        :poster-url="item.poster_url"
        :title="item.title"
        :kind-label="mediaKind(item.type)"
        :fiche-to="selecting ? null : `/medias/${item.id}`"
        :fallback="mediaKind(item.type)"
        :offline-ghost="offlineShell"
        :hide-more-button="selecting"
        @open="onCardOpen(item)"
      >
        <template v-if="selecting" #shell-extra>
          <label class="media-library-select-check check" @click.stop @pointerdown.stop>
            <input
              type="checkbox"
              aria-label="Sélectionner la fiche"
              :checked="isSelected(item.id)"
              @change="toggleSelected(item.id)"
            />
          </label>
        </template>
        <template #meta>
          <span class="hint media-library-kind">{{ mediaKind(item.type) }}</span>
          <strong>{{ item.title }}</strong>
          <span v-if="item.year || density === 'airy'" class="hint media-library-year">
            <template v-if="item.year">{{ item.year }}</template>
            <template v-if="item.year"> · </template>{{ mediaKind(item.type) }}
          </span>
          <span class="media-library-status icon-line" :class="`tone-${mediaBadge(item).name}`">
            <span class="media-library-status-dot" aria-hidden="true"></span>
            {{ mediaBadge(item).label }}
          </span>
          <span v-for="badge in hiveListBadges(item.id)" :key="badge.key" class="hive-list-badge" :class="`tone-${badge.tone}`">
            {{ badge.label }}
          </span>
          <span class="hint icon-line pc-card-icons">
            <StatusIcon v-if="item.followed" name="followed" label="Suivi" />
            <StatusIcon v-if="mediaOnDisk(item)" name="disk" label="Sur le disque" />
          </span>
        </template>
        <template #actions>
          <p class="card-actions">
            <a
              v-if="item.jellyfin_web_url"
              class="icon-button"
              :href="item.jellyfin_web_url"
              target="_blank"
              rel="noopener noreferrer"
              title="Lire sur Jellyfin"
            >
              <StatusIcon name="play" label="Lire sur Jellyfin" />
            </a>
          </p>
        </template>
      </PosterCard>
    </div>

    <p v-if="visible.length" class="hint media-library-end">
      Fin de liste · {{ formatTitleCount(filteredStats.total) }} affiché{{ filteredStats.total > 1 ? "s" : "" }} sur {{ formatTitleCount(libraryStats.total) }}
    </p>

    <div
      v-if="selecting && visible.length"
      class="media-library-bulk-bar media-library-bulk-bar--mobile actions"
      role="toolbar"
      aria-label="Sélection des fiches"
    >
      <span class="hint">{{ selectedCount }} sélectionnée{{ selectedCount > 1 ? "s" : "" }}</span>
      <button type="button" :disabled="!visible.length" @click="selectAllVisible">Tout sélectionner</button>
      <button type="button" @click="cancelSelection">Annuler</button>
      <button
        type="button"
        class="danger"
        :disabled="!selectedCount || deleteBusy || serverActionsDisabled"
        @click="deleteOpen = true"
      >
        Supprimer les fiches
      </button>
    </div>

    <DeleteFicheDialog
      :open="deleteOpen"
      :count="selectedCount"
      :titles="selectedTitlesForDialog"
      files-on-disk-untouched
      @cancel="deleteOpen = false"
      @confirm="deleteSelectedFiches"
    />

    <MediaQuickView :target="quick" @close="quick = null" @changed="onQuickViewChanged" />
  </section>
</template>

<style scoped>
.media-library-card.is-selected {
  outline: 2px solid var(--info);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}
.hive-list-badge {
  display: inline-flex;
  font-size: 0.74rem;
  font-weight: 700;
  margin-right: 0.35rem;
  color: #d9b46a;
}
.hive-list-badge.tone-muted {
  color: var(--muted);
}
.hive-list-badge.tone-info {
  color: var(--info);
}
</style>

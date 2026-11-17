<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import HomeFilterChips, { type HomeFilterKey } from "../components/HomeFilterChips.vue";
import HomeGridSection from "../components/HomeGridSection.vue";
import HomePersonalBlocks from "../components/HomePersonalBlocks.vue";
import HomeTechnicalFooter from "../components/HomeTechnicalFooter.vue";
import MediaQuickView from "../components/MediaQuickView.vue";
import PosterCard from "../components/PosterCard.vue";
import StatusIcon from "../components/StatusIcon.vue";
import TmdbPosterImg from "../components/TmdbPosterImg.vue";
import { api, ApiError, isNetworkError, isTransientHttpStatus, userFacingApiError } from "../api";
import { mediaKind, tmdbPage } from "../labels";
import type { QuickTarget } from "../quick";
import {
  loadStoredFilters,
  matchesMediaType,
  SECTION_ANCHORS,
  sectionVisible,
  storeFilters,
  type HomeSectionKey,
} from "../homeFilters";
import { useConnectivity } from "../pwa/connectivity";
import { loadDashboardSnapshot, saveDashboardSnapshot } from "../pwa/offlineSnapshot";
import { episodeSlot } from "../homeDashboardFormat";
import { formatParis } from "../time";

interface Card {
  media_id: number;
  media_title: string;
  media_type?: string;
  season: number | null;
  episode: number | null;
  title: string | null;
  air_date: string | null;
  downloaded: boolean;
  watched: boolean;
  poster_url?: string | null;
  still_url?: string | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

interface DownloadCard {
  id: number;
  state: string;
  progress: number;
  speed: number | null;
  eta_seconds: number | null;
  error_code: string | null;
  error: string | null;
  media_title: string | null;
  season: number | null;
  episode: number | null;
}

interface ProgressCard {
  media_id: number;
  media_title: string;
  watched: number;
  total: number;
  poster_url?: string | null;
  last_watched_at?: string | null;
  media_type?: string;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

interface AvailableCard {
  media_id: number;
  media_title: string;
  media_type?: string;
  item_id: number;
  season: number | null;
  episode: number | null;
  title: string | null;
  poster_url?: string | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

interface WatchGroup {
  media_id: number;
  media_title: string;
  media_type: string;
  count: number;
  label: string;
  poster_url?: string | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
  items: Card[];
}

interface LatestCard {
  title: string;
  subtitle: string | null;
  poster_url: string | null;
  media_type: string;
  media_id?: number | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
  tmdb_url?: string | null;
  catalog_id?: number | null;
  followed?: boolean;
  source_url?: string | null;
  in_library?: boolean;
  ignored?: boolean;
  hive_work_key?: string | null;
}

interface RecentDownloadCard {
  media_id: number;
  media_title: string;
  media_type: string;
  season: number | null;
  episode: number | null;
  title: string | null;
  episode_count?: number;
  poster_url?: string | null;
  downloaded_at: string;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}

interface UpcomingReleaseCard {
  title: string;
  poster_url: string | null;
  overview: string | null;
  media_type: string;
  release_on: string;
  tmdb_id: number;
  tmdb_url: string | null;
}

interface Dashboard {
  to_watch: Card[];
  to_watch_groups: WatchGroup[];
  upcoming_week: Card[];
  available: AvailableCard[];
  downloads_active: DownloadCard[];
  downloads_failed: DownloadCard[];
  progress: ProgressCard[];
  latest: LatestCard[];
  recent_downloads: RecentDownloadCard[];
  upcoming_releases: UpcomingReleaseCard[];
  catalog_films: LatestCard[];
  catalog_series: LatestCard[];
  hive_dispos: LatestCard[];
}

const route = useRoute();
const { offlineShell, serverActionsDisabled, serverReachable, probeServer } = useConnectivity();
const cacheNote = ref("");
const board = ref<Dashboard | null>(null);
const activeFilters = ref<HomeFilterKey[]>(loadStoredFilters());
const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);
const progressSort = ref<"name" | "watched">("name");
const expanded = ref<Partial<Record<HomeSectionKey, boolean>>>({});
const wide = ref(true);
let mediaQuery: MediaQueryList | null = null;


const error = ref("");
const notice = ref("");

const filteredAvailable = computed(() =>
  (board.value?.available ?? []).filter((card) => matchesMediaType(card.media_type, activeFilters.value)),
);
const filteredUpcomingReleases = computed(() =>
  (board.value?.upcoming_releases ?? []).filter((card) => matchesMediaType(card.media_type, activeFilters.value)),
);
const filteredCatalogFilms = computed(() =>
  activeFilters.value.length && !activeFilters.value.includes("films") ? [] : board.value?.catalog_films ?? [],
);
const filteredCatalogSeries = computed(() =>
  activeFilters.value.length && !activeFilters.value.includes("series") ? [] : board.value?.catalog_series ?? [],
);
const filteredHiveDispos = computed(() =>
  (board.value?.hive_dispos ?? []).filter((card) => matchesMediaType(card.media_type, activeFilters.value)),
);
const showHiveDisposSection = computed(() => Boolean(filteredHiveDispos.value.length));

function toggleFilter(key: HomeFilterKey) {
  const set = new Set(activeFilters.value);
  if (set.has(key)) {
    set.delete(key);
  } else {
    set.add(key);
  }
  activeFilters.value = [...set];
  storeFilters(activeFilters.value);
}

function showSection(key: HomeSectionKey): boolean {
  return sectionVisible(key, activeFilters.value);
}

function isExpanded(key: HomeSectionKey): boolean {
  return Boolean(expanded.value[key]);
}

function toggleSection(key: HomeSectionKey) {
  expanded.value[key] = !isExpanded(key);
}

function sliceSection<T>(key: HomeSectionKey, items: T[], limit?: number): T[] {
  if (isExpanded(key)) {
    return items;
  }
  const fallback = wide.value ? 7 : 6;
  const cap = limit ?? fallback;
  return items.slice(0, cap);
}

function releaseLabel(value: string): string {
  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  const label = parsed.toLocaleDateString("fr-FR", { day: "numeric", month: "short" });
  return `sortie ${label}`;
}

function syncWide() {
  wide.value = mediaQuery ? mediaQuery.matches : window.innerWidth >= 720;
}

onMounted(() => {
  mediaQuery = window.matchMedia("(min-width: 720px)");
  syncWide();
  mediaQuery.addEventListener("change", syncWide);
  void load().then(() => scrollToSectionAnchor());
});

onUnmounted(() => {
  mediaQuery?.removeEventListener("change", syncWide);
});

async function loadFromSnapshot(): Promise<boolean> {
  const snap = loadDashboardSnapshot<Dashboard>();
  if (!snap) {
    return false;
  }
  board.value = snap.body;
  cacheNote.value = `Affichage de la dernière visite (${formatParis(snap.saved_at)}). Les affiches ne sont pas conservées hors ligne.`;
  error.value = "";
  await nextTick();
  scrollToSectionAnchor();
  return true;
}

async function load() {
  error.value = "";
  try {
    board.value = await api<Dashboard>("/api/v1/dashboard");
    saveDashboardSnapshot(board.value);
    cacheNote.value = "";
    await nextTick();
    scrollToSectionAnchor();
  } catch (err) {
    const transient =
      isNetworkError(err) || (err instanceof ApiError && isTransientHttpStatus(err.status));
    if (transient) {
      await probeServer();
    }
    if (offlineShell.value || !serverReachable.value || transient) {
      if (await loadFromSnapshot()) {
        return;
      }
    }
    error.value = userFacingApiError(err, "Tableau de bord indisponible.");
  }
}

watch(
  () => offlineShell.value || !serverReachable.value,
  (down) => {
    if (down && !board.value) {
      void loadFromSnapshot();
    }
    if (!down && board.value && cacheNote.value) {
      void load();
    }
  },
);

function scrollToSectionAnchor() {
  const raw = route.hash?.replace(/^#/, "");
  if (!raw) {
    return;
  }
  document.getElementById(raw)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showQuick(target: QuickTarget, follow = false) {
  quickFollow.value = follow;
  quick.value = target;
}

function fromMedia(card: {
  media_id: number;
  media_title: string;
  media_type?: string;
  poster_url?: string | null;
  year?: number | null;
  overview?: string | null;
  tmdb_id?: number | null;
}): QuickTarget {
  return {
    title: card.media_title,
    year: card.year,
    overview: card.overview,
    posterUrl: card.poster_url,
    mediaType: card.media_type || "series",
    followed: true,
    tmdbId: card.tmdb_id,
    mediaId: card.media_id,
  };
}

async function followHive(card: LatestCard) {
  const key = card.hive_work_key?.trim();
  if (!key) {
    return;
  }
  error.value = "";
  notice.value = "";
  try {
    await api(`/api/v1/hive/catalogue/${key}/follow`, { method: "POST" });
    notice.value = "Œuvre suivie.";
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de suivre cette œuvre.");
  }
}

function fromCatalog(card: LatestCard, follow = false) {
  showQuick(
    {
      title: card.title,
      year: card.year,
      overview: card.overview,
      posterUrl: card.poster_url,
      mediaType: card.media_type,
      followed: card.followed,
      tmdbId: card.tmdb_id,
      tmdbUrl: card.tmdb_url,
      mediaId: card.media_id,
      catalogId: card.catalog_id,
      sourceUrl: card.source_url,
      ignored: card.ignored,
    },
    follow,
  );
}

function fromUpcoming(card: UpcomingReleaseCard, follow = false) {
  showQuick(
    {
      title: card.title,
      year: Number(card.release_on.slice(0, 4)) || null,
      overview: card.overview,
      posterUrl: card.poster_url,
      mediaType: card.media_type === "film" ? "film" : "series",
      followed: false,
      tmdbId: card.tmdb_id,
      tmdbUrl: card.tmdb_url || tmdbPage(card.tmdb_id, card.media_type),
    },
    follow,
  );
}

async function queue(itemId: number) {
  error.value = "";
  notice.value = "";
  try {
    await api("/api/v1/downloads", { method: "POST", body: JSON.stringify({ media_item_id: itemId }) });
    notice.value = "Ajouté à la file.";
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Téléchargement refusé.");
  }
}

async function queueAll() {
  const items = board.value?.available ?? [];
  if (!items.length) {
    return;
  }
  error.value = "";
  notice.value = "";
  let added = 0;
  for (const card of items) {
    try {
      await api("/api/v1/downloads", { method: "POST", body: JSON.stringify({ media_item_id: card.item_id }) });
      added += 1;
    } catch (err) {
      error.value = userFacingApiError(err, "Téléchargement refusé.");
      break;
    }
  }
  if (added) {
    notice.value = added > 1 ? `${added} ajouts à la file.` : "Ajouté à la file.";
  }
  await load();
}
</script>

<template>
  <section class="home-page stack">
    <h1>Accueil</h1>
    <p v-if="cacheNote" class="offline-cache-note">{{ cacheNote }}</p>
    <HomeFilterChips :active="activeFilters" @toggle="toggleFilter" />
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="notice" class="hint">{{ notice }}</p>

    <template v-if="board">
      <HomePersonalBlocks
        :board="board"
        :order="['toWatch', 'recentDownloads', 'upcomingWeek']"
        :wide="wide"
        :expanded="expanded"
        :active-filters="activeFilters"
        :offline-shell="offlineShell"
        @toggle="toggleSection"
        @open-quick="showQuick($event)"
      />

      <HomeGridSection
        v-show="showSection('available')"
        :section-id="SECTION_ANCHORS.available"
        title="À télécharger"
        :count="filteredAvailable.length"
        hint="Disponibles, pas encore téléchargés"
        :items-length="filteredAvailable.length"
        :expanded="isExpanded('available')"
        :wide="wide"
        @toggle="toggleSection('available')"
      >
        <template #actions>
          <button
            v-if="filteredAvailable.length"
            type="button"
            class="link-button"
            :disabled="serverActionsDisabled"
            :title="serverActionsDisabled ? 'Indisponible hors ligne' : undefined"
            @click="queueAll"
          >
            Tout télécharger
          </button>
        </template>
        <template #default="{ limit }">
          <p v-if="!filteredAvailable.length" class="hint">Rien à proposer.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('available', filteredAvailable, limit)"
              :key="`av-${card.item_id}`"
              :poster-url="card.poster_url"
              :title="card.media_title"
              :kind-label="mediaKind(card.media_type)"
              :fiche-to="`/medias/${card.media_id}`"
              :fallback="card.media_title"
              :offline-ghost="offlineShell"
              @open="showQuick(fromMedia(card))"
            >
              <template #meta>
                <strong>{{ card.media_title }}</strong>
                <span class="hint">{{ episodeSlot(card) }} · Disponible</span>
              </template>
              <template #actions>
                <p class="card-actions">
                  <button
                    type="button"
                    :disabled="serverActionsDisabled"
                    :title="serverActionsDisabled ? 'Indisponible hors ligne' : undefined"
                    @click="queue(card.item_id)"
                  >
                    Télécharger
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        v-show="showSection('upcomingReleases')"
        :section-id="SECTION_ANCHORS.upcomingReleases"
        title="À venir"
        :count="filteredUpcomingReleases.length"
        hint="Prochaines sorties TMDB (France)"
        :items-length="filteredUpcomingReleases.length"
        :expanded="isExpanded('upcomingReleases')"
        :wide="wide"
        @toggle="toggleSection('upcomingReleases')"
      >
        <template #default="{ limit }">
          <p v-if="!filteredUpcomingReleases.length" class="hint">Aucune sortie à venir.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('upcomingReleases', filteredUpcomingReleases, limit)"
              :key="`ur-${card.media_type}-${card.tmdb_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              :kind-label="mediaKind(card.media_type)"
              :fallback="card.title"
              :offline-ghost="offlineShell"
              @open="fromUpcoming(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ releaseLabel(card.release_on) }}</span>
              </template>
              <template #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromUpcoming(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        v-show="showSection('catalogFilms') && filteredCatalogFilms.length > 0"
        :section-id="SECTION_ANCHORS.catalogFilms"
        title="Derniers films"
        :count="filteredCatalogFilms.length"
        hint="Catalogue des sources"
        :items-length="filteredCatalogFilms.length"
        :expanded="isExpanded('catalogFilms')"
        :wide="wide"
        @toggle="toggleSection('catalogFilms')"
      >
        <template #default="{ limit }">
          <p v-if="!filteredCatalogFilms.length" class="hint">Pas encore de films dans le catalogue.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('catalogFilms', filteredCatalogFilms, limit)"
              :key="`cf-${card.catalog_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Film"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              :offline-ghost="offlineShell"
              @open="fromCatalog(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">
                  <template v-if="card.year">{{ card.year }} · </template>
                  <template v-if="card.subtitle">{{ card.subtitle }}</template>
                </span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromCatalog(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        v-show="showHiveDisposSection && showSection('hiveDispos')"
        :section-id="SECTION_ANCHORS.hiveDispos"
        title="Dernières dispos Hive"
        :count="filteredHiveDispos.length"
        hint="Partagées sur la ruche, pas encore possédées"
        :items-length="filteredHiveDispos.length"
        :expanded="isExpanded('hiveDispos')"
        :wide="wide"
        @toggle="toggleSection('hiveDispos')"
      >
        <template #default="{ limit }">
          <div class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('hiveDispos', filteredHiveDispos, limit)"
              :key="`hd-${card.hive_work_key}`"
              :poster-url="card.poster_url"
              :title="card.title"
              :kind-label="mediaKind(card.media_type)"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : { path: `/hive/catalogue/${card.hive_work_key}` }"
              :fallback="card.title"
              :offline-ghost="offlineShell"
              @open="fromCatalog(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">
                  <template v-if="card.year">{{ card.year }} · </template>
                  <template v-if="card.subtitle">{{ card.subtitle }}</template>
                </span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="followHive(card)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        v-show="showSection('catalogSeries') && filteredCatalogSeries.length > 0"
        :section-id="SECTION_ANCHORS.catalogSeries"
        title="Dernières séries"
        :count="filteredCatalogSeries.length"
        hint="Nouvelles séries et nouvelles saisons"
        :items-length="filteredCatalogSeries.length"
        :expanded="isExpanded('catalogSeries')"
        :wide="wide"
        @toggle="toggleSection('catalogSeries')"
      >
        <template #default="{ limit }">
          <p v-if="!filteredCatalogSeries.length" class="hint">Pas encore de séries dans le catalogue.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('catalogSeries', filteredCatalogSeries, limit)"
              :key="`cs-${card.catalog_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Série"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              :offline-ghost="offlineShell"
              @open="fromCatalog(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">
                  <template v-if="card.subtitle">{{ card.subtitle }}</template>
                </span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromCatalog(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomePersonalBlocks
        :board="board"
        :order="['progress']"
        :wide="wide"
        :expanded="expanded"
        :active-filters="activeFilters"
        :offline-shell="offlineShell"
        :progress-sort="progressSort"
        @update:progress-sort="progressSort = $event"
        @toggle="toggleSection"
        @open-quick="showQuick($event)"
      />

      <HomeTechnicalFooter :active="board.downloads_active" :failed="board.downloads_failed" />
    </template>

    <MediaQuickView :target="quick" :follow="quickFollow" @close="quick = null" @changed="load" />
  </section>
</template>

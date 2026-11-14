<script setup lang="ts">
import { computed } from "vue";

import HomeGridSection from "./HomeGridSection.vue";
import HomeTechnicalFooter from "./HomeTechnicalFooter.vue";
import PosterCard from "./PosterCard.vue";
import StatusIcon from "./StatusIcon.vue";
import { progressPercent, recentDownloadMeta, upcomingEpisodeMeta } from "../homeDashboardFormat";
import type { HomePersonalSectionKey, HomePersonalDashboardSlice } from "../homeDashboardTypes";
import { matchesMediaType, SECTION_ANCHORS, sectionVisible, type HomeSectionKey } from "../homeFilters";
import type { HomeFilterKey } from "./HomeFilterChips.vue";
import { mediaKind } from "../labels";
import type { QuickTarget } from "../quick";
import { parisDayKey } from "../time";

const props = withDefaults(
  defineProps<{
    board: HomePersonalDashboardSlice;
    order: HomePersonalSectionKey[];
    wide: boolean;
    expanded: Partial<Record<HomeSectionKey, boolean>>;
    activeFilters?: HomeFilterKey[];
    offlineShell?: boolean;
    hideWhenEmpty?: boolean;
    showDownloadProgress?: boolean;
    progressSort?: "name" | "watched";
    toWatchEmptyLink?: "home" | "none";
  }>(),
  {
    activeFilters: () => [],
    offlineShell: false,
    hideWhenEmpty: false,
    showDownloadProgress: false,
    progressSort: "name",
    toWatchEmptyLink: "home",
  },
);

const emit = defineEmits<{
  toggle: [key: HomeSectionKey];
  openQuick: [target: QuickTarget];
  "update:progressSort": [value: "name" | "watched"];
}>();

const todayKey = computed(() => parisDayKey(new Date().toISOString()));
const yesterdayKey = computed(() => {
  const day = new Date();
  day.setDate(day.getDate() - 1);
  return parisDayKey(day.toISOString());
});

const filteredToWatchGroups = computed(() =>
  (props.board.to_watch_groups ?? []).filter((group) => matchesMediaType(group.media_type, props.activeFilters)),
);
const filteredRecentDownloads = computed(() =>
  (props.board.recent_downloads ?? []).filter((card) => matchesMediaType(card.media_type, props.activeFilters)),
);
const filteredUpcomingWeek = computed(() =>
  (props.board.upcoming_week ?? []).filter((card) => matchesMediaType(card.media_type, props.activeFilters)),
);
const progressRows = computed(() => {
  const rows = [...(props.board.progress ?? [])];
  if (props.progressSort === "watched") {
    rows.sort(
      (left, right) =>
        (right.last_watched_at || "").localeCompare(left.last_watched_at || "") ||
        left.media_title.localeCompare(right.media_title, "fr"),
    );
  } else {
    rows.sort((left, right) => left.media_title.localeCompare(right.media_title, "fr"));
  }
  return rows;
});
const filteredProgressRows = computed(() =>
  progressRows.value.filter((row) => matchesMediaType(row.media_type, props.activeFilters)),
);

const showDownloadsFooter = computed(
  () =>
    props.showDownloadProgress &&
    (props.board.downloads_active.length > 0 || props.board.downloads_failed.length > 0),
);

function showSection(key: HomeSectionKey): boolean {
  return sectionVisible(key, props.activeFilters);
}

function isExpanded(key: HomeSectionKey): boolean {
  return Boolean(props.expanded[key]);
}

function sliceSection<T>(key: HomeSectionKey, items: T[], limit?: number): T[] {
  if (isExpanded(key)) {
    return items;
  }
  const fallback = props.wide ? 7 : 6;
  const cap = limit ?? fallback;
  return items.slice(0, cap);
}

function sectionShown(key: HomeSectionKey, count: number): boolean {
  if (!showSection(key)) {
    return false;
  }
  if (props.hideWhenEmpty && count === 0) {
    return false;
  }
  return true;
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

function wantsSection(personal: HomePersonalSectionKey): boolean {
  return props.order.includes(personal);
}
</script>

<template>
  <template v-for="section in order" :key="section">
    <HomeTechnicalFooter
      v-if="section === 'progress' && showDownloadsFooter && wantsSection('progress')"
      :active="board.downloads_active"
      :failed="board.downloads_failed"
    />

    <HomeGridSection
      v-if="section === 'progress' && sectionShown('progress', filteredProgressRows.length)"
      v-show="showSection('progress')"
      :section-id="SECTION_ANCHORS.progress"
      title="Progression"
      :count="filteredProgressRows.length"
      hint="Vue sur l'état"
      :items-length="filteredProgressRows.length"
      :expanded="isExpanded('progress')"
      :wide="wide"
      @toggle="emit('toggle', 'progress')"
    >
      <template #actions>
        <label class="home-progress-sort">
          <span class="hint">Tri</span>
          <select
            :value="progressSort"
            @change="emit('update:progressSort', ($event.target as HTMLSelectElement).value as 'name' | 'watched')"
          >
            <option value="name">Nom</option>
            <option value="watched">Dernier visionnage</option>
          </select>
        </label>
      </template>
      <template #default="{ limit }">
        <p v-if="!filteredProgressRows.length" class="hint">Aucun épisode suivi.</p>
        <div v-else class="poster-grid pc-grid pc-grid--home progress-grid">
          <PosterCard
            v-for="row in sliceSection('progress', filteredProgressRows, limit)"
            :key="`p-${row.media_id}`"
            :poster-url="row.poster_url"
            :title="row.media_title"
            :kind-label="mediaKind(row.media_type || 'series')"
            :fiche-to="`/medias/${row.media_id}`"
            :fallback="row.media_title"
            :offline-ghost="offlineShell"
            @open="emit('openQuick', fromMedia(row))"
          >
            <template #meta>
              <strong>{{ row.media_title }}</strong>
              <span class="hint icon-line">
                <StatusIcon name="watched" :label="`${row.watched} vu${row.watched > 1 ? 's' : ''}`" />
                {{ row.watched }}/{{ row.total }}
              </span>
              <span class="track" aria-hidden="true">
                <span class="fill" :style="{ width: `${progressPercent(row.watched, row.total)}%` }"></span>
              </span>
            </template>
          </PosterCard>
        </div>
      </template>
    </HomeGridSection>

    <HomeGridSection
      v-if="section === 'upcomingWeek' && sectionShown('upcomingWeek', filteredUpcomingWeek.length)"
      v-show="showSection('upcomingWeek')"
      :section-id="SECTION_ANCHORS.upcomingWeek"
      title="Prochains épisodes"
      :count="filteredUpcomingWeek.length"
      hint="Séries suivies · 7 jours"
      :items-length="filteredUpcomingWeek.length"
      :expanded="isExpanded('upcomingWeek')"
      :wide="wide"
      @toggle="emit('toggle', 'upcomingWeek')"
    >
      <template #default="{ limit }">
        <p v-if="!filteredUpcomingWeek.length" class="hint">Aucune diffusion sur 7 jours.</p>
        <div v-else class="poster-grid pc-grid pc-grid--home">
          <PosterCard
            v-for="card in sliceSection('upcomingWeek', filteredUpcomingWeek, limit)"
            :key="`up-${card.media_id}-${card.season}-${card.episode}`"
            :poster-url="card.poster_url"
            :title="card.media_title"
            :kind-label="mediaKind(card.media_type || 'series')"
            :fiche-to="`/medias/${card.media_id}`"
            :fallback="card.media_title"
            :offline-ghost="offlineShell"
            @open="emit('openQuick', fromMedia(card))"
          >
            <template #meta>
              <strong>{{ card.media_title }}</strong>
              <span class="hint">{{ upcomingEpisodeMeta(card, todayKey, yesterdayKey) }}</span>
            </template>
          </PosterCard>
        </div>
      </template>
    </HomeGridSection>

    <HomeGridSection
      v-if="section === 'recentDownloads' && sectionShown('recentDownloads', filteredRecentDownloads.length)"
      v-show="showSection('recentDownloads')"
      :section-id="SECTION_ANCHORS.recentDownloads"
      title="Derniers téléchargements"
      :count="filteredRecentDownloads.length"
      hint="Arrivés dans la bibliothèque"
      :items-length="filteredRecentDownloads.length"
      :expanded="isExpanded('recentDownloads')"
      :wide="wide"
      @toggle="emit('toggle', 'recentDownloads')"
    >
      <template #default="{ limit }">
        <p v-if="!filteredRecentDownloads.length" class="hint">
          Aucun téléchargement. <router-link to="/suivre">Suivre</router-link>
        </p>
        <div v-else class="poster-grid pc-grid pc-grid--home">
          <PosterCard
            v-for="card in sliceSection('recentDownloads', filteredRecentDownloads, limit)"
            :key="`rd-${card.media_id}-${card.season}-${card.episode}-${card.downloaded_at}`"
            :poster-url="card.poster_url"
            :title="card.media_title"
            :kind-label="mediaKind(card.media_type || 'series')"
            :fiche-to="`/medias/${card.media_id}`"
            :fallback="card.media_title"
            :offline-ghost="offlineShell"
            @open="emit('openQuick', fromMedia(card))"
          >
            <template #meta>
              <strong>{{ card.media_title }}</strong>
              <span class="hint home-card-meta">{{ recentDownloadMeta(card, todayKey, yesterdayKey) }}</span>
            </template>
          </PosterCard>
        </div>
      </template>
    </HomeGridSection>

    <HomeGridSection
      v-if="section === 'toWatch' && sectionShown('toWatch', filteredToWatchGroups.length)"
      v-show="showSection('toWatch')"
      :section-id="SECTION_ANCHORS.toWatch"
      title="À voir"
      :count="filteredToWatchGroups.length"
      hint="Prêt à regarder"
      :items-length="filteredToWatchGroups.length"
      :expanded="isExpanded('toWatch')"
      :wide="wide"
      @toggle="emit('toggle', 'toWatch')"
    >
      <template #default="{ limit }">
        <p v-if="!filteredToWatchGroups.length" class="hint">
          Tout est vu.
          <router-link
            v-if="toWatchEmptyLink === 'home'"
            :to="{ path: '/', hash: `#${SECTION_ANCHORS.upcomingReleases}` }"
          >
            Voir les prochaines sorties
          </router-link>
        </p>
        <div v-else class="poster-grid pc-grid pc-grid--home">
          <PosterCard
            v-for="group in sliceSection('toWatch', filteredToWatchGroups, limit)"
            :key="`g-${group.media_id}`"
            :poster-url="group.poster_url"
            :title="group.media_title"
            :kind-label="mediaKind(group.media_type)"
            :fiche-to="`/medias/${group.media_id}`"
            fallback="Série"
            :offline-ghost="offlineShell"
            @open="emit('openQuick', fromMedia(group))"
          >
            <template v-if="group.count > 1" #overlay>
              <span class="poster-badge">{{ group.count }}</span>
            </template>
            <template #meta>
              <strong>{{ group.media_title }}</strong>
              <span class="hint">{{ group.label }}</span>
            </template>
          </PosterCard>
        </div>
      </template>
    </HomeGridSection>
  </template>
</template>

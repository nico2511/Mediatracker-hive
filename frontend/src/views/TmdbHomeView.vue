<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";

import HomeGridSection from "../components/HomeGridSection.vue";
import HomePersonalBlocks from "../components/HomePersonalBlocks.vue";
import MediaQuickView from "../components/MediaQuickView.vue";
import PosterCard from "../components/PosterCard.vue";
import StatusIcon from "../components/StatusIcon.vue";
import { api, userFacingApiError } from "../api";
import type { HomePersonalDashboardSlice } from "../homeDashboardTypes";
import type { HomeSectionKey } from "../homeFilters";
import { mediaKind } from "../labels";
import type { QuickTarget } from "../quick";
import { tmdbTrialRatingLabel } from "../tmdbTrialRating";

interface TmdbCard {
  tmdb_id: number;
  media_type: string;
  title: string;
  year?: number | null;
  overview?: string | null;
  poster_url?: string | null;
  vote_average?: number | null;
  subtitle?: string | null;
  followed?: boolean;
  in_library?: boolean;
  media_id?: number | null;
  tmdb_url?: string | null;
  hive_work_key?: string | null;
}

interface TmdbHome {
  cinema_films: TmdbCard[];
  streaming_films: TmdbCard[];
  physical_films: TmdbCard[];
  new_series: TmdbCard[];
  on_air_series: TmdbCard[];
  hive_dispos: TmdbCard[];
}

type SectionKey = "cinema" | "streaming" | "physical" | "newSeries" | "onAir" | "hive";

const board = ref<TmdbHome | null>(null);
const personalBoard = ref<HomePersonalDashboardSlice | null>(null);
const error = ref("");
const loading = ref(false);
const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);
const expanded = ref<Partial<Record<SectionKey, boolean>>>({});
const personalExpanded = ref<Partial<Record<HomeSectionKey, boolean>>>({});
const progressSort = ref<"name" | "watched">("name");
const wide = ref(true);
let mediaQuery: MediaQueryList | null = null;

function syncWide() {
  wide.value = mediaQuery ? mediaQuery.matches : window.innerWidth >= 720;
}

function togglePersonalSection(key: HomeSectionKey) {
  personalExpanded.value[key] = !personalExpanded.value[key];
}

function isExpanded(key: SectionKey): boolean {
  return Boolean(expanded.value[key]);
}

function toggleSection(key: SectionKey) {
  expanded.value[key] = !isExpanded(key);
}

function sliceSection<T>(key: SectionKey, items: T[]): T[] {
  if (isExpanded(key)) {
    return items;
  }
  const cap = wide.value ? 7 : 6;
  return items.slice(0, cap);
}

function showQuick(target: QuickTarget, follow = false) {
  quickFollow.value = follow;
  quick.value = target;
}

function fromTmdb(card: TmdbCard, follow = false) {
  const kind = card.media_type === "film" ? "movie" : "tv";
  showQuick(
    {
      title: card.title,
      year: card.year ?? null,
      overview: card.overview ?? null,
      posterUrl: card.poster_url ?? null,
      mediaType: card.media_type,
      followed: Boolean(card.followed),
      tmdbId: card.tmdb_id,
      tmdbUrl: card.tmdb_url || `https://www.themoviedb.org/${kind}/${card.tmdb_id}`,
      mediaId: card.media_id ?? null,
    },
    follow,
  );
}

async function followHive(card: TmdbCard) {
  const key = card.hive_work_key?.trim();
  if (!key) {
    return;
  }
  error.value = "";
  try {
    await api(`/api/v1/hive/catalogue/${key}/follow`, { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de suivre cette œuvre.");
  }
}

function metaHint(card: TmdbCard): string {
  const parts: string[] = [];
  if (card.year) {
    parts.push(String(card.year));
  }
  parts.push(mediaKind(card.media_type));
  const rating = tmdbTrialRatingLabel(card.vote_average ?? null);
  if (rating) {
    parts.push(`TMDB ${rating}/10`);
  }
  return parts.join(" · ");
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [tmdbHome, dashboard] = await Promise.all([
      api<TmdbHome>("/api/v1/tmdb-trial/home"),
      api<HomePersonalDashboardSlice & Record<string, unknown>>("/api/v1/dashboard"),
    ]);
    board.value = tmdbHome;
    personalBoard.value = {
      to_watch_groups: dashboard.to_watch_groups ?? [],
      upcoming_week: dashboard.upcoming_week ?? [],
      recent_downloads: dashboard.recent_downloads ?? [],
      progress: dashboard.progress ?? [],
      downloads_active: dashboard.downloads_active ?? [],
      downloads_failed: dashboard.downloads_failed ?? [],
    };
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement impossible.");
    board.value = null;
    personalBoard.value = null;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  mediaQuery = window.matchMedia("(min-width: 720px)");
  syncWide();
  mediaQuery.addEventListener("change", syncWide);
  void load();
});

onUnmounted(() => {
  mediaQuery?.removeEventListener("change", syncWide);
});
</script>

<template>
  <section class="stack home-page">
    <div class="row">
      <h1>Accueil</h1>
    </div>
    <p class="hint">Rangées alimentées par TMDB (France, fr-FR).</p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="loading" class="hint">Chargement…</p>

    <HomePersonalBlocks
      v-if="personalBoard"
      :board="personalBoard"
      :order="['progress', 'upcomingWeek', 'recentDownloads', 'toWatch']"
      :wide="wide"
      :expanded="personalExpanded"
      hide-when-empty
      show-download-progress
      to-watch-empty-link="none"
      :progress-sort="progressSort"
      @update:progress-sort="progressSort = $event"
      @toggle="togglePersonalSection"
      @open-quick="showQuick($event)"
    />

    <template v-if="board">
      <HomeGridSection
        :section-id="'tmdb-cinema'"
        title="Films au cinéma"
        :count="board.cinema_films.length"
        hint="Sorties salles (types TMDB 2 et 3, région FR)"
        :items-length="board.cinema_films.length"
        :expanded="isExpanded('cinema')"
        :wide="wide"
        @toggle="toggleSection('cinema')"
      >
        <template #default>
          <p v-if="!board.cinema_films.length" class="hint">Aucun film pour le moment.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('cinema', board.cinema_films)"
              :key="`ci-${card.tmdb_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Film"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              @open="fromTmdb(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ metaHint(card) }}</span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromTmdb(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        :section-id="'tmdb-streaming'"
        title="Films en streaming / VOD"
        :count="board.streaming_films.length"
        hint="Type 4, plateformes FR"
        :items-length="board.streaming_films.length"
        :expanded="isExpanded('streaming')"
        :wide="wide"
        @toggle="toggleSection('streaming')"
      >
        <template #default>
          <p v-if="!board.streaming_films.length" class="hint">Aucun film pour le moment.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('streaming', board.streaming_films)"
              :key="`st-${card.tmdb_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Film"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              @open="fromTmdb(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ metaHint(card) }}</span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromTmdb(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        :section-id="'tmdb-physical'"
        title="Films DVD / Blu-ray"
        :count="board.physical_films.length"
        hint="Type 5, région FR"
        :items-length="board.physical_films.length"
        :expanded="isExpanded('physical')"
        :wide="wide"
        @toggle="toggleSection('physical')"
      >
        <template #default>
          <p v-if="!board.physical_films.length" class="hint">Aucun film pour le moment.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('physical', board.physical_films)"
              :key="`ph-${card.tmdb_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Film"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              @open="fromTmdb(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ metaHint(card) }}</span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromTmdb(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        :section-id="'tmdb-new-series'"
        title="Nouvelles séries"
        :count="board.new_series.length"
        hint="Première diffusion récente (watch_region FR)"
        :items-length="board.new_series.length"
        :expanded="isExpanded('newSeries')"
        :wide="wide"
        @toggle="toggleSection('newSeries')"
      >
        <template #default>
          <p v-if="!board.new_series.length" class="hint">Aucune série pour le moment.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('newSeries', board.new_series)"
              :key="`ns-${card.tmdb_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Série"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              @open="fromTmdb(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ metaHint(card) }}</span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromTmdb(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        :section-id="'tmdb-on-air'"
        title="Séries en cours / à l'antenne"
        :count="board.on_air_series.length"
        hint="Épisodes diffusés en France (air_date -14 j / +7 j)"
        :items-length="board.on_air_series.length"
        :expanded="isExpanded('onAir')"
        :wide="wide"
        @toggle="toggleSection('onAir')"
      >
        <template #default>
          <p v-if="!board.on_air_series.length" class="hint">Aucune série pour le moment.</p>
          <div v-else class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('onAir', board.on_air_series)"
              :key="`oa-${card.tmdb_id}`"
              :poster-url="card.poster_url"
              :title="card.title"
              kind-label="Série"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
              :fallback="card.title"
              @open="fromTmdb(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ metaHint(card) }}</span>
                <span v-if="card.followed || card.in_library" class="hint icon-line">
                  <StatusIcon v-if="card.followed" name="followed" label="Suivi" />
                  <StatusIcon v-if="card.in_library" name="disk" label="En bibliothèque" />
                </span>
              </template>
              <template v-if="!card.followed" #actions>
                <p class="card-actions">
                  <button type="button" class="icon-button" title="Suivre" @click="fromTmdb(card, true)">
                    <StatusIcon name="followed" label="Suivre" />
                  </button>
                </p>
              </template>
            </PosterCard>
          </div>
        </template>
      </HomeGridSection>

      <HomeGridSection
        v-if="board.hive_dispos.length"
        :section-id="'tmdb-hive-dispos'"
        title="Dernières dispos Hive"
        :count="board.hive_dispos.length"
        hint="Identique à l'accueil classique"
        :items-length="board.hive_dispos.length"
        :expanded="isExpanded('hive')"
        :wide="wide"
        @toggle="toggleSection('hive')"
      >
        <template #default>
          <div class="poster-grid pc-grid pc-grid--home">
            <PosterCard
              v-for="card in sliceSection('hive', board.hive_dispos)"
              :key="`hd-${card.hive_work_key}`"
              :poster-url="card.poster_url"
              :title="card.title"
              :kind-label="mediaKind(card.media_type)"
              :fiche-to="card.media_id ? `/medias/${card.media_id}` : { path: `/hive/catalogue/${card.hive_work_key}` }"
              :fallback="card.title"
              @open="fromTmdb(card)"
            >
              <template #meta>
                <strong>{{ card.title }}</strong>
                <span class="hint">{{ metaHint(card) }}</span>
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
    </template>

    <MediaQuickView :target="quick" :follow="quickFollow" @close="quick = null" @changed="load" />
  </section>
</template>

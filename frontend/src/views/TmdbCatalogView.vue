<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import MediaQuickView from "../components/MediaQuickView.vue";
import PosterCard from "../components/PosterCard.vue";
import StatusIcon from "../components/StatusIcon.vue";
import { api, userFacingApiError } from "../api";
import { mediaKind, tmdbPage } from "../labels";
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
  followed?: boolean;
  in_library?: boolean;
  media_id?: number | null;
  tmdb_url?: string | null;
}

interface Provider {
  id: number;
  name: string;
}

interface BrowsePage {
  items: TmdbCard[];
  total: number;
  page: number;
  genres: string[];
  years: number[];
  providers: Provider[];
}

const props = defineProps<{ mediaType: "film" | "series" }>();

const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);
const items = ref<TmdbCard[]>([]);
const total = ref(0);
const page = ref(1);
const genres = ref<string[]>([]);
const years = ref<number[]>([]);
const providers = ref<Provider[]>([]);
const query = ref("");
const genre = ref("");
const year = ref("");
const followed = ref("");
const library = ref("");
const releaseKind = ref(props.mediaType === "film" ? "streaming" : "new");
const sortMode = ref<"recent" | "popularity">("recent");
const providerId = ref("");
const error = ref("");
const loading = ref(false);

const titleOf = computed(() => (props.mediaType === "series" ? "Dernières séries" : "Derniers films"));

const releaseOptions = computed(() =>
  props.mediaType === "film"
    ? [
        { value: "cinema", label: "Cinéma" },
        { value: "streaming", label: "Streaming / VOD" },
        { value: "physical", label: "DVD / Blu-ray" },
      ]
    : [
        { value: "new", label: "Nouvelles séries" },
        { value: "on_air", label: "À l'antenne" },
      ],
);

const pageCount = computed(() => Math.max(1, Math.ceil(total.value / 60)));

function showQuick(target: QuickTarget, follow = false) {
  quickFollow.value = follow;
  quick.value = target;
}

function fromCard(card: TmdbCard, follow = false) {
  showQuick(
    {
      title: card.title,
      year: card.year ?? null,
      overview: card.overview ?? null,
      posterUrl: card.poster_url ?? null,
      mediaType: card.media_type,
      followed: Boolean(card.followed),
      tmdbId: card.tmdb_id,
      tmdbUrl: card.tmdb_url || tmdbPage(card.tmdb_id, card.media_type),
      mediaId: card.media_id ?? null,
    },
    follow,
  );
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
    const params = new URLSearchParams({
      type: props.mediaType,
      page: String(page.value),
      limit: "60",
      release_kind: releaseKind.value,
      sort: sortMode.value,
    });
    if (query.value.trim()) {
      params.set("q", query.value.trim());
    }
    if (genre.value) {
      params.set("genre", genre.value);
    }
    if (year.value) {
      params.set("year", year.value);
    }
    if (providerId.value && props.mediaType === "film") {
      params.set("provider", providerId.value);
    }
    if (followed.value === "yes") {
      params.set("followed", "true");
    } else if (followed.value === "no") {
      params.set("followed", "false");
    }
    if (library.value === "yes") {
      params.set("in_library", "true");
    } else if (library.value === "no") {
      params.set("in_library", "false");
    }
    const data = await api<BrowsePage>(`/api/v1/tmdb-trial/browse?${params}`);
    items.value = data.items;
    total.value = data.total;
    page.value = data.page;
    genres.value = data.genres;
    years.value = data.years;
    providers.value = data.providers;
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement impossible.");
    items.value = [];
  } finally {
    loading.value = false;
  }
}

function prevPage() {
  if (page.value > 1) {
    page.value -= 1;
    void load();
  }
}

function nextPage() {
  if (page.value < pageCount.value) {
    page.value += 1;
    void load();
  }
}

onMounted(load);
watch(
  () => props.mediaType,
  () => {
    page.value = 1;
    query.value = "";
    genre.value = "";
    year.value = "";
    followed.value = "";
    library.value = "";
    providerId.value = "";
    releaseKind.value = props.mediaType === "film" ? "streaming" : "new";
    sortMode.value = "recent";
    void load();
  },
);
</script>

<template>
  <section class="stack">
    <div class="row">
      <h1>{{ titleOf }}</h1>
    </div>
    <p class="hint">Grille TMDB uniquement — sans scan catalogue ni réglages de sources.</p>
    <p v-if="error" class="error">{{ error }}</p>

    <form class="stack" @submit.prevent="() => { page = 1; load(); }">
      <div class="filters">
        <label>
          <span>Recherche</span>
          <input v-model="query" type="search" />
        </label>
        <label>
          <span>Type de sortie</span>
          <select v-model="releaseKind">
            <option v-for="opt in releaseOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <label>
          <span>Tri</span>
          <select v-model="sortMode">
            <option value="recent">Plus récents</option>
            <option value="popularity">Popularité</option>
          </select>
        </label>
        <button type="submit" :disabled="loading">Filtrer</button>
      </div>
      <details class="panel">
        <summary>Filtres</summary>
        <div class="filters">
          <label v-if="mediaType === 'film'">
            <span>Plateforme (FR)</span>
            <select v-model="providerId">
              <option value="">Toutes</option>
              <option v-for="prov in providers" :key="prov.id" :value="String(prov.id)">{{ prov.name }}</option>
            </select>
          </label>
          <label>
            <span>Genre</span>
            <select v-model="genre">
              <option value="">Tous</option>
              <option v-for="name in genres" :key="name" :value="name">{{ name }}</option>
            </select>
          </label>
          <label>
            <span>Année</span>
            <select v-model="year">
              <option value="">Toutes</option>
              <option v-for="value in years" :key="value" :value="String(value)">{{ value }}</option>
            </select>
          </label>
          <label>
            <span>Suivi</span>
            <select v-model="followed">
              <option value="">Tous</option>
              <option value="yes">Déjà suivis</option>
              <option value="no">Pas encore suivis</option>
            </select>
          </label>
          <label>
            <span>Bibliothèque</span>
            <select v-model="library">
              <option value="">Tous</option>
              <option value="yes">Déjà dans la bibliothèque</option>
              <option value="no">Absents de la bibliothèque</option>
            </select>
          </label>
        </div>
      </details>
    </form>

    <p class="hint">{{ total }} titre(s) approximatif(s) · page {{ page }} / {{ pageCount }}</p>
    <div class="row">
      <button type="button" :disabled="loading || page <= 1" @click="prevPage">Page précédente</button>
      <button type="button" :disabled="loading || page >= pageCount" @click="nextPage">Page suivante</button>
    </div>

    <p v-if="loading" class="hint">Chargement…</p>
    <p v-else-if="!items.length" class="hint">Aucun résultat pour ces filtres.</p>

    <div v-else class="poster-grid pc-grid catalog-grid">
      <PosterCard
        v-for="card in items"
        :key="card.tmdb_id"
        :poster-url="card.poster_url"
        :title="card.title"
        :kind-label="mediaKind(card.media_type)"
        :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
        :fallback="card.title"
        @open="fromCard(card)"
      >
        <template #meta>
          <strong>{{ card.title }}</strong>
          <span class="hint">{{ metaHint(card) }}</span>
          <span class="hint icon-line">
            <StatusIcon v-if="card.followed" name="followed" label="Déjà suivi" />
            <StatusIcon v-if="card.in_library" name="disk" label="Dans la bibliothèque" />
          </span>
        </template>
        <template #actions>
          <p class="card-actions">
            <button
              v-if="!card.followed"
              type="button"
              class="icon-button"
              title="Suivre"
              @click="fromCard(card, true)"
            >
              <StatusIcon name="followed" label="Suivre" />
            </button>
            <a
              v-if="card.tmdb_url"
              class="icon-button"
              :href="card.tmdb_url"
              target="_blank"
              rel="noreferrer"
              title="TMDB"
            >
              <StatusIcon name="search" label="TMDB" />
            </a>
          </p>
        </template>
      </PosterCard>
    </div>

    <MediaQuickView :target="quick" :follow="quickFollow" @close="quick = null" @changed="load" />
  </section>
</template>

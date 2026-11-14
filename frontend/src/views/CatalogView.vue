<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import MediaQuickView from "../components/MediaQuickView.vue";
import PosterCard from "../components/PosterCard.vue";
import StatusIcon from "../components/StatusIcon.vue";
import {api, userFacingApiError} from "../api";
import { patchSettings } from "../settings";
import { mediaKind } from "../labels";
import type { QuickTarget } from "../quick";
import { formatParis } from "../time";

interface CatalogSource {
  source: string;
  url: string;
}

interface CatalogCard {
  id: number;
  title: string;
  year: number | null;
  media_type: string;
  season: number | null;
  episode: number | null;
  quality: string | null;
  language: string | null;
  source: string;
  source_url: string;
  sources: CatalogSource[];
  first_seen_at: string;
  tmdb_id: number | null;
  tmdb_url: string | null;
  poster_url: string | null;
  overview: string | null;
  rating: number | null;
  genres: string[];
  ignored: boolean;
  followed: boolean;
  in_library: boolean;
  media_id?: number | null;
}

interface CatalogPage {
  items: CatalogCard[];
  total: number;
  last_refresh_at: string | null;
  next_refresh_at: string | null;
  last_error: string | null;
  genres: string[];
  years: number[];
  news_sources_usable?: boolean;
  notice?: string | null;
}

interface DigitalRelease {
  title: string;
  poster_url: string | null;
  overview: string | null;
  date: string;
  type: "movie" | "tv";
  tmdb_id: number;
  tracked: boolean;
  media_id?: number | null;
}

interface CatalogStatus {
  interval_hours: number;
  retention_days: number;
  quality: string;
  language: string;
  news_sources_usable?: boolean;
  notice?: string | null;
}

const props = defineProps<{ mediaType: "film" | "series" }>();
const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);

const items = ref<CatalogCard[]>([]);
const total = ref(0);
const genres = ref<string[]>([]);
const years = ref<number[]>([]);
const lastRefresh = ref<string | null>(null);
const nextRefresh = ref<string | null>(null);
const lastError = ref<string | null>(null);
const query = ref("");
const genre = ref("");
const year = ref("");
const followed = ref("");
const library = ref("");
const showIgnored = ref(false);
const error = ref("");
const message = ref("");
const loading = ref(false);
const refreshing = ref(false);
const quality = ref("HD");
const language = ref("VFF");
const intervalHours = ref(12);
const retentionDays = ref(60);
const saving = ref(false);
const catalogNotice = ref<string | null>(null);

const qualities = ["HD", "1080p", "4K", "HDLight", "SD"];
const languages = ["VFF", "VFQ", "VF", "MULTI", "VOSTFR", "VO"];

onMounted(load);
watch(() => props.mediaType, () => {
  query.value = "";
  genre.value = "";
  year.value = "";
  followed.value = "";
  library.value = "";
  load();
});

function emptyHint(): string {
  if (catalogNotice.value) {
    return catalogNotice.value;
  }
  const reason = (lastError.value || "").toLowerCase();
  if (reason.includes("n'est pas configurée") || reason.includes("aucune source")) {
    return "L'URL Wawacity est vide. Renseignez-la dans Réglages, puis actualisez.";
  }
  if (reason.includes("cloudflare")) {
    return "Wawacity a renvoyé un contrôle Cloudflare. La prochaine tentative est repoussée.";
  }
  if (reason.includes("repouss") || reason.includes("429")) {
    return "Les requêtes sont en pause après une série d'erreurs. Regardez l'heure de la prochaine actualisation.";
  }
  if (query.value || genre.value || year.value || followed.value || library.value) {
    return "Aucune nouveauté pour ces filtres. Élargissez la recherche ou videz un filtre.";
  }
  return "Aucune nouveauté pour le moment.";
}

function titleOf(): string {
  return props.mediaType === "series" ? "Nouveautés séries" : "Nouveautés films";
}

function formatWhen(value: string | null): string {
  return formatParis(value) || "jamais";
}

function seasonLabel(card: CatalogCard): string {
  if (card.season == null) {
    return "";
  }
  if (card.episode == null) {
    return `Saison ${card.season}`;
  }
  const season = String(card.season).padStart(2, "0");
  const episode = String(card.episode).padStart(2, "0");
  return `S${season}E${episode}`;
}

function ratingLabel(value: number | null): string {
  if (value == null) {
    return "";
  }
  return value.toLocaleString("fr-FR", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
}

const digital = ref<DigitalRelease[]>([]);
const releases = computed(() =>
  digital.value.filter((row) => (props.mediaType === "film" ? row.type === "movie" : row.type === "tv")),
);

function showQuick(target: QuickTarget, follow = false) {
  quickFollow.value = follow;
  quick.value = target;
}

function fromCatalog(card: CatalogCard, follow = false) {
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
      catalogId: card.id,
      sourceUrl: card.source_url,
      ignored: card.ignored,
      season: card.season,
    },
    follow,
  );
}

function releaseMediaType(row: DigitalRelease): string {
  return row.type === "tv" ? "series" : "film";
}

function releaseTmdbUrl(row: DigitalRelease): string {
  const kind = row.type === "tv" ? "tv" : "movie";
  return `https://www.themoviedb.org/${kind}/${row.tmdb_id}`;
}

function fromRelease(row: DigitalRelease, follow = false) {
  const kind = row.type === "tv" ? "tv" : "movie";
  showQuick(
    {
      title: row.title,
      year: Number(row.date.slice(0, 4)) || null,
      overview: row.overview,
      posterUrl: row.poster_url,
      mediaType: row.type,
      followed: row.tracked,
      tmdbId: row.tmdb_id,
      tmdbUrl: `https://www.themoviedb.org/${kind}/${row.tmdb_id}`,
      mediaId: row.media_id,
    },
    follow,
  );
}

async function loadDigital() {
  try {
    const page = await api<{ items: DigitalRelease[] }>("/api/v1/ha/releases");
    digital.value = page.items;
  } catch {
    digital.value = [];
  }
}

async function loadStatus() {
  const status = await api<CatalogStatus>("/api/v1/catalog/status");
  quality.value = status.quality;
  language.value = status.language;
  intervalHours.value = status.interval_hours;
  retentionDays.value = status.retention_days;
  catalogNotice.value = status.notice?.trim() || null;
}

async function load() {
  loading.value = true;
  error.value = "";
  try {
    await loadStatus();
    const params = new URLSearchParams({ type: props.mediaType, limit: "60" });
    if (query.value.trim()) {
      params.set("q", query.value.trim());
    }
    if (genre.value) {
      params.set("genre", genre.value);
    }
    if (year.value) {
      params.set("year", year.value);
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
    if (showIgnored.value) {
      params.set("include_ignored", "true");
    }
    await loadDigital();
    const page = await api<CatalogPage>(`/api/v1/catalog?${params}`);
    items.value = page.items;
    total.value = page.total;
    genres.value = page.genres;
    years.value = page.years;
    lastRefresh.value = page.last_refresh_at;
    nextRefresh.value = page.next_refresh_at;
    lastError.value = page.last_error;
    if (page.notice?.trim()) {
      catalogNotice.value = page.notice.trim();
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement impossible.");
  } finally {
    loading.value = false;
  }
}

async function refreshCatalog() {
  refreshing.value = true;
  error.value = "";
  message.value = "";
  try {
    const report = await api<{ added: number; updated: number; error: string | null }>("/api/v1/catalog/refresh", {
      method: "POST",
    });
    message.value = report.error
      ? report.error
      : `${report.added} ajout(s), ${report.updated} déjà connu(s).`;
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Actualisation impossible.");
  } finally {
    refreshing.value = false;
  }
}

async function saveSettings() {
  saving.value = true;
  error.value = "";
  message.value = "";
  try {
    await patchSettings({
      values: {
        catalog_quality: quality.value,
        catalog_language: language.value,
        catalog_interval_hours: intervalHours.value,
        catalog_retention_days: retentionDays.value,
      },
    });
    message.value = "Réglages du catalogue enregistrés.";
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Enregistrement impossible.");
  } finally {
    saving.value = false;
  }
}

async function refreshLists() {
  await Promise.all([load(), loadDigital()]);
}

async function ignore(card: CatalogCard) {
  error.value = "";
  try {
    await api(`/api/v1/catalog/${card.id}/ignore`, { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de masquer cette entrée.");
  }
}

async function restore(card: CatalogCard) {
  error.value = "";
  try {
    await api(`/api/v1/catalog/${card.id}/restore`, { method: "POST" });
    await load();
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de réafficher cette entrée.");
  }
}
</script>

<template>
  <section class="stack">
    <div class="row">
      <h1>{{ titleOf() }}</h1>
      <button type="button" :disabled="refreshing" @click="refreshCatalog">
        {{ refreshing ? "Actualisation…" : "Actualiser" }}
      </button>
    </div>
    <p class="hint">
      Dernière actualisation : {{ formatWhen(lastRefresh) }}.
      Prochaine : {{ formatWhen(nextRefresh) }}.
      {{ total }} entrée(s).
    </p>
    <p v-if="lastError" class="hint">{{ lastError }}</p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="message" class="hint">{{ message }}</p>

    <section v-if="releases.length" class="catalog-releases stack">
      <h2>{{ mediaType === "film" ? "Sorties numériques" : "Séries à l'antenne" }}</h2>
      <p class="hint">
        {{
          mediaType === "film"
            ? "Sorties digitales TMDB (France). Un appui sur l'affiche ouvre la fiche."
            : "Nouvelles séries et nouvelles saisons diffusées en France. Un appui ouvre la fiche."
        }}
      </p>
      <div class="poster-grid pc-grid catalog-grid">
        <PosterCard
          v-for="row in releases"
          :key="`${row.type}-${row.tmdb_id}`"
          :poster-url="row.poster_url"
          :title="row.title"
          :kind-label="mediaKind(releaseMediaType(row))"
          :fiche-to="row.media_id ? `/medias/${row.media_id}` : null"
          :fallback="mediaKind(releaseMediaType(row))"
          @open="fromRelease(row)"
        >
          <template #meta>
            <strong>{{ row.title }}</strong>
            <span class="hint">{{ mediaKind(releaseMediaType(row)) }} · {{ row.date }}</span>
            <span v-if="row.tracked" class="hint icon-line">
              <StatusIcon name="followed" label="Suivi" />
            </span>
          </template>
          <template #actions>
            <p class="card-actions">
              <button
                v-if="!row.tracked"
                type="button"
                class="icon-button"
                title="Suivre"
                @click="fromRelease(row, true)"
              >
                <StatusIcon name="followed" label="Suivre" />
              </button>
              <a class="icon-button" :href="releaseTmdbUrl(row)" target="_blank" rel="noreferrer" title="TMDB">
                <StatusIcon name="search" label="TMDB" />
              </a>
            </p>
          </template>
        </PosterCard>
      </div>
    </section>

    <form @submit.prevent="load">
      <div class="filters">
        <label>
          <span>Recherche</span>
          <input v-model="query" type="search" />
        </label>
        <button type="submit" :disabled="loading">Filtrer</button>
      </div>
      <details class="panel">
        <summary>Filtres</summary>
        <div class="filters">
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
          <label class="check">
            <input v-model="showIgnored" type="checkbox" />
            <span>Afficher les entrées masquées</span>
          </label>
        </div>
      </details>
    </form>

    <p v-if="loading" class="hint">Chargement…</p>
    <p v-else-if="!items.length" class="hint">{{ emptyHint() }}</p>

    <div v-else class="poster-grid pc-grid catalog-grid">
      <PosterCard
        v-for="card in items"
        :key="card.id"
        :poster-url="card.poster_url"
        :title="card.title"
        :kind-label="mediaKind(card.media_type)"
        :fiche-to="card.media_id ? `/medias/${card.media_id}` : null"
        :fallback="card.title"
        @open="fromCatalog(card)"
      >
        <template #meta>
          <strong>{{ card.title }}</strong>
          <span class="hint">
            <template v-if="card.year">{{ card.year }} · </template>{{ mediaKind(card.media_type) }}
            <template v-if="seasonLabel(card)"> · {{ seasonLabel(card) }}</template>
            <template v-if="card.quality"> · {{ card.quality }}</template>
            <template v-if="card.language"> · {{ card.language }}</template>
          </span>
          <span class="hint icon-line">
            <StatusIcon v-if="card.followed" name="followed" label="Déjà suivi" />
            <StatusIcon v-if="card.in_library" name="disk" label="Dans la bibliothèque" />
            <StatusIcon v-if="card.ignored" name="pause" label="Masqué" />
          </span>
        </template>
        <template #actions>
          <p class="card-actions">
            <button
              v-if="!card.followed && !card.ignored"
              type="button"
              class="icon-button"
              title="Suivre"
              @click="fromCatalog(card, true)"
            >
              <StatusIcon name="followed" label="Suivre" />
            </button>
            <a class="icon-button" :href="card.source_url" target="_blank" rel="noreferrer" title="Source">
              <StatusIcon name="link" label="Source" />
            </a>
            <button v-if="!card.ignored" type="button" class="icon-button" title="Masquer" @click="ignore(card)">
              <StatusIcon name="hide" label="Masquer" />
            </button>
            <button v-else type="button" class="icon-button" title="Réafficher" @click="restore(card)">
              <StatusIcon name="eye" label="Réafficher" />
            </button>
            <a v-if="card.tmdb_url" class="icon-button" :href="card.tmdb_url" target="_blank" rel="noreferrer" title="TMDB">
              <StatusIcon name="search" label="TMDB" />
            </a>
          </p>
        </template>
      </PosterCard>
    </div>
    <MediaQuickView :target="quick" :follow="quickFollow" @close="quick = null" @changed="refreshLists" />

    <details class="panel">
      <summary>Réglages du catalogue</summary>
      <form class="stack" @submit.prevent="saveSettings">
        <p class="hint">
          Le filtre s'applique à toutes les sources. Vide = tout garder. L'actualisation automatique
          reste au plus deux fois par jour par défaut, avec la même pause entre les requêtes que le scan.
        </p>
        <label>
          <span>Qualité</span>
          <select v-model="quality">
            <option value="">Toutes</option>
            <option v-for="value in qualities" :key="value" :value="value">{{ value }}</option>
          </select>
        </label>
        <label>
          <span>Langue</span>
          <select v-model="language">
            <option value="">Toutes</option>
            <option v-for="value in languages" :key="value" :value="value">{{ value }}</option>
          </select>
        </label>
        <label>
          <span>Intervalle (heures)</span>
          <input v-model.number="intervalHours" type="number" min="1" max="168" required />
        </label>
        <label>
          <span>Conservation (jours)</span>
          <input v-model.number="retentionDays" type="number" min="1" max="3650" required />
        </label>
        <button type="submit" :disabled="saving">Enregistrer</button>
      </form>
    </details>
  </section>
</template>

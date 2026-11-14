<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import {api, userFacingApiError} from "../api";
import type { QuickTarget } from "../quick";
import StatusIcon from "./StatusIcon.vue";
import TmdbPosterImg from "./TmdbPosterImg.vue";

const props = defineProps<{ target: QuickTarget | null; follow?: boolean }>();
const emit = defineEmits<{ close: []; changed: [] }>();
const router = useRouter();

interface MediaQuick {
  id: number;
  type: string;
  title: string;
  year: number | null;
  overview: string | null;
  poster_url: string | null;
  followed: boolean;
  tmdb_id: number | null;
  tmdb_url: string | null;
  jellyfin_web_url?: string | null;
  catalog_id?: number | null;
  source_url?: string | null;
  cleanup_reason?: string | null;
}

interface SeasonBrief {
  number: number;
  name: string | null;
  episode_count: number | null;
}

const extra = ref<Partial<QuickTarget>>({});
const cleanupReason = ref<string | null>(null);
const followOpen = ref(false);
const policy = ref<"propose" | "auto">("propose");
const selected = ref<number[]>([]);
const seasons = ref<SeasonBrief[]>([]);
const seasonsNote = ref("");
const busy = ref(false);
const error = ref("");
const notice = ref("");

const view = computed(() => {
  const base = props.target;
  if (!base) {
    return null;
  }
  const over = extra.value;
  return {
    title: over.title || base.title,
    year: over.year ?? base.year ?? null,
    overview: over.overview || base.overview || null,
    posterUrl: over.posterUrl || base.posterUrl || null,
    mediaType: over.mediaType || base.mediaType,
    followed: over.followed ?? Boolean(base.followed),
    tmdbUrl: over.tmdbUrl || base.tmdbUrl || null,
    tmdbId: over.tmdbId ?? base.tmdbId ?? null,
    mediaId: base.mediaId ?? over.mediaId ?? null,
    catalogId: over.catalogId ?? base.catalogId ?? null,
    sourceUrl: over.sourceUrl || base.sourceUrl || null,
    jellyfinUrl: over.jellyfinUrl || base.jellyfinUrl || null,
    ignored: Boolean(base.ignored),
    season: base.season ?? null,
  };
});

const series = computed(() => {
  const kind = view.value?.mediaType;
  return kind === "series" || kind === "tv";
});

const needsSeason = computed(() => series.value && seasons.value.length > 0 && selected.value.length === 0);

watch(
  () => [props.target, props.follow] as const,
  async ([target, follow]) => {
    extra.value = {};
    cleanupReason.value = null;
    error.value = "";
    notice.value = "";
    followOpen.value = Boolean(follow && target && !target.followed);
    if (followOpen.value) {
      void prepareFollow();
    }
    if (!target?.mediaId) {
      return;
    }
    try {
      const media = await api<MediaQuick>(`/api/v1/media/${target.mediaId}`);
      if (props.target?.mediaId !== target.mediaId) {
        return;
      }
      cleanupReason.value = media.cleanup_reason || null;
      extra.value = {
        title: media.title,
        year: media.year,
        overview: media.overview,
        posterUrl: media.poster_url,
        mediaType: media.type,
        followed: media.followed,
        tmdbId: media.tmdb_id,
        tmdbUrl: media.tmdb_url,
        jellyfinUrl: media.jellyfin_web_url,
        catalogId: media.catalog_id,
        sourceUrl: media.source_url,
        mediaId: media.id,
      };
    } catch {
      /* the card already carries a title and a poster */
    }
  },
  { immediate: true },
);

function openFollow() {
  error.value = "";
  notice.value = "";
  followOpen.value = true;
  void prepareFollow();
}

async function prepareFollow() {
  policy.value = "propose";
  seasons.value = [];
  seasonsNote.value = "";
  selected.value = [];
  const current = view.value;
  if (!current || !series.value || !current.tmdbId) {
    return;
  }
  try {
    const hit = await api<{ seasons?: SeasonBrief[] }>(`/api/v1/metadata/tmdb/series/${current.tmdbId}`);
    seasons.value = hit.seasons || [];
    const numbers = seasons.value.map((season) => season.number);
    const regular = numbers.filter((number) => number > 0);
    const initial = regular.length ? [...regular] : [...numbers];
    if (current.season != null && numbers.includes(current.season) && !initial.includes(current.season)) {
      initial.push(current.season);
    }
    selected.value = initial;
    if (!seasons.value.length) {
      seasonsNote.value = "TMDB n'a pas renvoyé de saisons. Toutes les saisons seront suivies.";
    }
  } catch {
    seasonsNote.value = "Les saisons TMDB sont indisponibles. Toutes les saisons seront suivies.";
  }
}

function seasonLabel(season: SeasonBrief): string {
  if (season.number === 0) {
    return season.episode_count != null ? `Spéciaux · ${season.episode_count}` : "Spéciaux";
  }
  const count = season.episode_count != null ? ` · ${season.episode_count}` : "";
  return `S${String(season.number).padStart(2, "0")}${count}`;
}

async function confirm(startNow: boolean) {
  const current = view.value;
  if (!current || needsSeason.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  const body: Record<string, unknown> = { start_now: startNow };
  if (series.value) {
    body.new_episode_policy = policy.value;
    if (seasons.value.length) {
      body.seasons = [...selected.value];
    }
  }
  try {
    let mediaId = current.mediaId;
    if (current.catalogId) {
      const created = await api<{ id: number }>(`/api/v1/catalog/${current.catalogId}/follow`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      mediaId = created.id;
    } else if (current.mediaId) {
      const followed = await api<{ id: number }>(`/api/v1/media/${current.mediaId}/follow`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      mediaId = followed.id;
    } else {
      const created = await api<{ id: number }>("/api/v1/media", {
        method: "POST",
        body: JSON.stringify({
          ...body,
          type: series.value ? "series" : "film",
          title: current.title,
          year: current.year ?? undefined,
          tmdb_id: current.tmdbId ?? undefined,
          overview: current.overview ?? undefined,
          poster_url: current.posterUrl ?? undefined,
        }),
      });
      mediaId = created.id;
    }
    followOpen.value = false;
    notice.value = startNow ? "Suivi enregistré, recherche lancée." : "Suivi enregistré.";
    extra.value = { ...extra.value, followed: true };
    emit("changed");
  } catch (err) {
    error.value = userFacingApiError(err, "Suivi impossible.");
  } finally {
    busy.value = false;
  }
}

async function hide() {
  const current = view.value;
  if (!current?.catalogId) {
    return;
  }
  error.value = "";
  try {
    await api(`/api/v1/catalog/${current.catalogId}/ignore`, { method: "POST" });
    emit("changed");
    emit("close");
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de masquer.");
  }
}

async function restore() {
  const current = view.value;
  if (!current?.catalogId) {
    return;
  }
  error.value = "";
  try {
    await api(`/api/v1/catalog/${current.catalogId}/restore`, { method: "POST" });
    emit("changed");
    emit("close");
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de réafficher.");
  }
}

function openFiche() {
  const id = view.value?.mediaId;
  if (id == null) {
    return;
  }
  emit("close");
  void router.push(`/medias/${id}`);
}

function onKey(event: KeyboardEvent) {
  if (!props.target || event.key !== "Escape") {
    return;
  }
  if (followOpen.value) {
    followOpen.value = false;
    return;
  }
  emit("close");
}

onMounted(() => document.addEventListener("keydown", onKey));
onUnmounted(() => document.removeEventListener("keydown", onKey));
</script>

<template>
  <Teleport to="body">
    <div v-if="view" class="dialog-backdrop" role="presentation" @click.self="emit('close')">
      <div class="panel dialog quick" role="dialog" aria-modal="true" aria-labelledby="quick-title">
        <div class="quick-layout">
          <TmdbPosterImg v-if="view.posterUrl" :url="view.posterUrl" />
          <span v-else class="poster-fallback">{{ view.title }}</span>
          <div>
            <h2 id="quick-title">{{ view.title }}</h2>
            <p class="hint">
              <template v-if="view.year">{{ view.year }} · </template>
              {{ series ? "Série" : "Film" }}
            </p>
            <p class="quick-overview">{{ view.overview || "Pas de synopsis." }}</p>
            <p v-if="cleanupReason" class="hint">{{ cleanupReason }}</p>
          </div>
        </div>
        <p v-if="notice && !followOpen" class="hint" role="status">{{ notice }}</p>
        <p v-if="error && !followOpen" class="error">{{ error }}</p>
        <p class="actions">
          <template v-if="!view.followed">
            <button type="button" class="primary" @click="openFollow">Suivre</button>
            <a v-if="view.tmdbUrl" class="button" :href="view.tmdbUrl" target="_blank" rel="noopener noreferrer">TMDB</a>
            <button v-if="view.mediaId" type="button" class="button" @click="openFiche">Fiche</button>
          </template>
          <template v-else>
            <button v-if="view.mediaId" type="button" class="button" @click="openFiche">Fiche</button>
            <a
              v-if="view.jellyfinUrl"
              class="button"
              :href="view.jellyfinUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              Lire sur Jellyfin
            </a>
            <a v-if="view.sourceUrl" class="button" :href="view.sourceUrl" target="_blank" rel="noopener noreferrer">
              Source
            </a>
            <button v-if="view.catalogId && !view.ignored" type="button" @click="hide">Masquer</button>
          </template>
          <button v-if="view.ignored && view.catalogId" type="button" @click="restore">Réafficher</button>
          <button type="button" @click="emit('close')">Fermer</button>
        </p>
      </div>
    </div>
    <div v-if="view && followOpen" class="dialog-backdrop" role="presentation" @click.self="followOpen = false">
      <form class="panel dialog follow" role="dialog" aria-modal="true" aria-labelledby="follow-title" @submit.prevent>
        <h2 id="follow-title">Suivre {{ view.title }}</h2>
        <p>Commencer à télécharger maintenant ?</p>
        <fieldset v-if="series">
          <legend>Saisons à télécharger</legend>
          <p v-if="seasonsNote" class="hint">{{ seasonsNote }}</p>
          <div v-if="seasons.length" class="season-picks">
            <label v-for="season in seasons" :key="season.number" class="check">
              <input v-model="selected" type="checkbox" :value="season.number" />
              <span>{{ seasonLabel(season) }}</span>
            </label>
          </div>
          <p v-if="needsSeason" class="hint">Choisissez au moins une saison.</p>
        </fieldset>
        <fieldset v-if="series">
          <legend>Nouveaux épisodes</legend>
          <label class="check">
            <input v-model="policy" type="radio" value="propose" />
            <span>Me demander</span>
          </label>
          <label class="check">
            <input v-model="policy" type="radio" value="auto" />
            <span>Automatique</span>
          </label>
        </fieldset>
        <p v-if="error" class="error">{{ error }}</p>
        <p class="actions">
          <button type="button" class="primary" :disabled="busy || needsSeason" @click="confirm(true)">
            {{ busy ? "En cours…" : "Oui" }}
          </button>
          <button type="button" :disabled="busy || needsSeason" @click="confirm(false)">
            {{ busy ? "En cours…" : "Non" }}
          </button>
          <button type="button" @click="followOpen = false">Annuler</button>
        </p>
      </form>
    </div>
  </Teleport>
</template>

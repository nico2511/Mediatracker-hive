<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import CollapsibleSection from "../components/CollapsibleSection.vue";
import OfflineFollowPanel from "../components/OfflineFollowPanel.vue";
import TmdbPosterImg from "../components/TmdbPosterImg.vue";
import {api, userFacingApiError} from "../api";
import { useConnectivity } from "../pwa/connectivity";
import { fetchSettings } from "../settings";
import { mediaKind, tmdbPage, tmdbSearch } from "../labels";

interface Hit {
  tmdb_id: number;
  media_type: string;
  title: string;
  year: number | null;
  overview: string | null;
  poster_url: string | null;
  followed?: boolean;
  in_library?: boolean;
}

const router = useRouter();
const route = useRoute();
const { mode, syncResults } = useConnectivity();

const showOfflinePanel = computed(() => {
  if (syncResults.value?.length) {
    return true;
  }
  if (mode.value !== "online") {
    return true;
  }
  return route.query.file === "1";
});
const tokenSet = ref(false);
const query = ref("");
const kind = ref<"series" | "film">("series");
const hits = ref<Hit[]>([]);
const tmdbUrl = ref("");
const refTitle = ref("");
const refYear = ref("");
const title = ref("");
const year = ref("");
const error = ref("");
const message = ref("");
let searchTimer = 0;

onMounted(async () => {
  const body = await fetchSettings();
  tokenSet.value = body.settings.find((row) => row.key === "tmdb_read_token")?.set ?? false;
});

function scheduleSearch() {
  window.clearTimeout(searchTimer);
  if (query.value.trim().length < 2) {
    hits.value = [];
    return;
  }
  searchTimer = window.setTimeout(() => void search(), 350);
}

async function search() {
  error.value = "";
  const text = query.value.trim();
  if (text.length < 2) {
    hits.value = [];
    return;
  }
  try {
    const params = new URLSearchParams({ q: text, type: "multi" });
    const body = await api<{ items: Hit[] }>(`/api/v1/metadata/tmdb/search?${params}`);
    const wanted = kind.value;
    hits.value = body.items.filter((hit) => hit.media_type === wanted);
    if (!hits.value.length) {
      message.value = "Aucun résultat.";
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Recherche impossible.");
  }
}

function openHit(event: MouseEvent, hit: Hit) {
  const target = event.target as HTMLElement | null;
  if (target?.closest("button, select, a")) {
    return;
  }
  window.open(tmdbPage(hit.tmdb_id, hit.media_type), "_blank", "noopener,noreferrer");
}

function followRef() {
  const raw = tmdbUrl.value.trim();
  const payload: Record<string, unknown> = {};
  if (/^\d+$/.test(raw)) {
    payload.tmdb_id = Number(raw);
    payload.type = kind.value;
  } else {
    payload.tmdb_url = raw;
  }
  if (!tokenSet.value) {
    payload.title = refTitle.value;
    if (refYear.value) {
      payload.year = Number(refYear.value);
    }
  }
  return follow(payload);
}

async function follow(payload: Record<string, unknown>) {
  error.value = "";
  message.value = "";
  try {
    const created = await api<{ id: number }>("/api/v1/media", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    await router.push(`/medias/${created.id}`);
  } catch (err) {
    error.value = userFacingApiError(err, "Suivi impossible.");
  }
}
</script>

<template>
  <OfflineFollowPanel v-if="showOfflinePanel" />
  <section v-else class="stack">
    <h1>Suivre</h1>
    <p class="hint">
      Ajoutez un film ou une série (TMDB ou titre). Les sources sont consultées à partir de la date de diffusion,
      puis selon l'intervalle configuré.
    </p>
    <p v-if="!tokenSet" class="hint">Jeton TMDB absent : la recherche par titre est désactivée.</p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="message" class="hint">{{ message }}</p>

    <CollapsibleSection v-if="tokenSet" title="Chercher un titre" :default-open="true">
      <form class="stack" @submit.prevent="search">
        <label>
          <span>Titre</span>
          <input v-model="query" minlength="2" @input="scheduleSearch" />
        </label>
        <label class="field-compact">
          <span>Type</span>
          <select v-model="kind" class="select-compact">
            <option value="series">Série</option>
            <option value="film">Film</option>
          </select>
        </label>
        <p class="actions">
          <button type="submit">Chercher</button>
          <a
            v-if="query.trim().length >= 2"
            class="button-link"
            :href="tmdbSearch(query)"
            target="_blank"
            rel="noreferrer"
          >Chercher sur TMDB ↗</a>
        </p>
        <ul v-if="hits.length" class="entry-list">
          <li v-for="hit in hits" :key="hit.tmdb_id" @click="openHit($event, hit)">
            <a class="entry-main" :href="tmdbPage(hit.tmdb_id, hit.media_type)" target="_blank" rel="noreferrer">
              <TmdbPosterImg v-if="hit.poster_url" class="entry-poster" :url="hit.poster_url" thumb />
              <span v-else class="entry-poster entry-poster-empty" aria-hidden="true"></span>
              <span class="entry-copy">
                <strong>{{ hit.title }}</strong>
                <span class="hint">
                  <template v-if="hit.year">{{ hit.year }} · </template>{{ mediaKind(hit.media_type) }}
                  <template v-if="hit.in_library"> · Dans la bibliothèque</template>
                  <template v-if="hit.followed"> · Déjà suivi</template>
                </span>
              </span>
            </a>
            <div class="entry-actions">
              <button type="button" @click="follow({ tmdb_id: hit.tmdb_id, type: hit.media_type })">Suivre</button>
            </div>
          </li>
        </ul>
      </form>
    </CollapsibleSection>

    <CollapsibleSection title="URL ou identifiant TMDB">
      <form class="stack" @submit.prevent="followRef">
        <label>
          <span>URL ou identifiant</span>
          <input v-model="tmdbUrl" placeholder="https://www.themoviedb.org/tv/12345 ou 12345" required />
        </label>
        <label class="field-compact">
          <span>Type (pour un identifiant numérique)</span>
          <select v-model="kind" class="select-compact">
            <option value="series">Série</option>
            <option value="film">Film</option>
          </select>
        </label>
        <template v-if="!tokenSet">
          <label>
            <span>Titre</span>
            <input v-model="refTitle" required />
          </label>
          <label>
            <span>Année</span>
            <input v-model="refYear" inputmode="numeric" />
          </label>
        </template>
        <button type="submit">Suivre</button>
      </form>
    </CollapsibleSection>

    <CollapsibleSection v-if="!tokenSet" title="Sans TMDB">
      <form
        class="stack"
        @submit.prevent="follow({ type: kind, title, year: year ? Number(year) : null })"
      >
        <label>
          <span>Titre</span>
          <input v-model="title" required />
        </label>
        <label>
          <span>Année</span>
          <input v-model="year" inputmode="numeric" />
        </label>
        <label class="field-compact">
          <span>Type</span>
          <select v-model="kind" class="select-compact">
            <option value="series">Série</option>
            <option value="film">Film</option>
          </select>
        </label>
        <button type="submit">Suivre</button>
      </form>
    </CollapsibleSection>
  </section>
</template>

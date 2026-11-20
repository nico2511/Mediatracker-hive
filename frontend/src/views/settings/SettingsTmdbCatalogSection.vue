<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";

import { api, userFacingApiError } from "../../api";
import { fetchSettings, patchSettings } from "../../settings";
import {
  TMDB_DISPLAY_LANGUAGES,
  TMDB_ORIGINAL_LANGUAGE_CHIPS,
  TMDB_REGIONS,
  tmdbDisplayLanguageLabel,
  tmdbRegionLabel,
} from "./tmdbCatalogMeta";

interface PreviewCard {
  poster_url: string | null;
  title: string;
}

const displayLanguage = ref("fr-FR");
const region = ref("FR");
const originalLanguages = ref<string[]>([]);
const message = ref("");
const error = ref("");
const saving = ref(false);
const previewLoading = ref(false);
const preview = ref<PreviewCard[]>([]);
let previewTimer: ReturnType<typeof setTimeout> | undefined;

function parseLanguages(raw: unknown): string[] {
  if (typeof raw !== "string" || !raw.trim()) {
    return [];
  }
  return raw.split(",").map((part) => part.trim().toLowerCase()).filter(Boolean);
}

async function load() {
  error.value = "";
  try {
    const page = await fetchSettings();
    const map = new Map(page.settings?.map((row) => [row.key, row.value]));
    const lang = map.get("tmdb_display_language");
    displayLanguage.value = typeof lang === "string" && lang ? lang : "fr-FR";
    const reg = map.get("tmdb_region");
    region.value = typeof reg === "string" && reg ? reg : "FR";
    originalLanguages.value = parseLanguages(map.get("tmdb_original_languages"));
    queuePreview();
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement impossible.");
  }
}

function toggleOriginal(code: string) {
  const set = new Set(originalLanguages.value);
  if (set.has(code)) {
    set.delete(code);
  } else {
    set.add(code);
  }
  originalLanguages.value = [...set];
  queuePreview();
}

function queuePreview() {
  if (previewTimer) {
    clearTimeout(previewTimer);
  }
  previewTimer = setTimeout(() => {
    void refreshPreview();
  }, 350);
}

async function refreshPreview() {
  previewLoading.value = true;
  try {
    const langs = originalLanguages.value.join(",");
    const query = new URLSearchParams({
      display_language: displayLanguage.value,
      region: region.value,
    });
    if (langs) {
      query.set("original_languages", langs);
    }
    const data = await api<{ items: PreviewCard[] }>(`/api/v1/tmdb-trial/preview?${query.toString()}`);
    preview.value = data.items ?? [];
  } catch {
    preview.value = [];
  } finally {
    previewLoading.value = false;
  }
}

async function save() {
  saving.value = true;
  message.value = "";
  error.value = "";
  try {
    await patchSettings({
      values: {
        tmdb_display_language: displayLanguage.value,
        tmdb_region: region.value,
        tmdb_original_languages: originalLanguages.value.join(","),
      },
    });
    message.value = "Réglages enregistrés.";
    await refreshPreview();
  } catch (err) {
    error.value = userFacingApiError(err, "Enregistrement impossible.");
  } finally {
    saving.value = false;
  }
}

watch(displayLanguage, queuePreview);
watch(region, queuePreview);

void load();

onUnmounted(() => {
  if (previewTimer) {
    clearTimeout(previewTimer);
  }
});
</script>

<template>
  <section class="settings-section panel stack tmdb-catalog-settings" data-testid="tmdb-catalog-settings">
    <h2 class="settings-section-title">Catalogue TMDB</h2>
    <p class="hint">Ces choix n’affectent pas le filtre de version (VFF, VOSTFR, etc.) des sources de téléchargement.</p>
    <form class="stack" @submit.prevent="save">
      <label>
        <span>Langue d’affichage</span>
        <select v-model="displayLanguage" data-testid="tmdb-display-language">
          <option v-for="row in TMDB_DISPLAY_LANGUAGES" :key="row.code" :value="row.code">
            {{ tmdbDisplayLanguageLabel(row.code) }}
          </option>
        </select>
      </label>
      <label>
        <span>Région</span>
        <select v-model="region" data-testid="tmdb-region">
          <option v-for="row in TMDB_REGIONS" :key="row.code" :value="row.code">
            {{ tmdbRegionLabel(row.code) }}
          </option>
        </select>
        <span class="hint">Dates de sortie et plateformes de diffusion.</span>
      </label>
      <h3 class="settings-subtitle">Langues originales</h3>
      <p class="hint">
        Aucune sélection = toutes les langues. Le filtre s’applique aux rangées Découvrir, Au cinéma, En cours et à la
        recherche du catalogue TMDB (filtrage côté serveur), pas à l’identification d’un fichier ou d’un titre précis.
      </p>
      <ul class="tmdb-lang-chips" data-testid="tmdb-original-languages">
        <li v-for="chip in TMDB_ORIGINAL_LANGUAGE_CHIPS" :key="chip.code">
          <button
            type="button"
            class="chip"
            :class="{ 'chip--on': originalLanguages.includes(chip.code) }"
            :aria-pressed="originalLanguages.includes(chip.code)"
            @click="toggleOriginal(chip.code)"
          >
            {{ chip.label }}
          </button>
        </li>
      </ul>
      <div class="tmdb-preview-block">
        <h3 class="settings-subtitle">Aperçu</h3>
        <p v-if="previewLoading" class="hint">Aperçu en cours…</p>
        <div v-else class="tmdb-preview-row" data-testid="tmdb-preview-row">
          <figure v-for="(card, index) in preview" :key="index" class="tmdb-preview-card">
            <img v-if="card.poster_url" :src="card.poster_url" alt="" loading="lazy" />
            <span class="tmdb-preview-title">{{ card.title }}</span>
          </figure>
        </div>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="message">{{ message }}</p>
      <button type="submit" :disabled="saving">Enregistrer les réglages Catalogue TMDB</button>
    </form>
  </section>
</template>

<style scoped>
.tmdb-lang-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  list-style: none;
  margin: 0;
  padding: 0;
}
.chip {
  border: 1px solid var(--border, #444);
  border-radius: 999px;
  background: transparent;
  padding: 0.35rem 0.75rem;
  cursor: pointer;
}
.chip--on {
  background: var(--accent, #3b82f6);
  color: #fff;
  border-color: transparent;
}
.tmdb-preview-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 0.75rem;
}
@media (max-width: 720px) {
  .tmdb-preview-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    overflow-x: auto;
  }
}
.tmdb-preview-card {
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.tmdb-preview-card img {
  width: 100%;
  border-radius: 4px;
  aspect-ratio: 2 / 3;
  object-fit: cover;
  background: #222;
}
.tmdb-preview-title {
  font-size: 0.85rem;
  line-height: 1.2;
}
</style>

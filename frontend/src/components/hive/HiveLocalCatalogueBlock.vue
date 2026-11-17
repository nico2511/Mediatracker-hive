<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { api, userFacingApiError } from "../../api";
import HiveCatalogueVersionList, { type HiveCatalogueVersionRow } from "./HiveCatalogueVersionList.vue";

export interface HiveLocalCatalogueWork {
  work_key: string;
  type: string | null;
  season: number | null;
  episode: number | null;
  versions: HiveCatalogueVersionRow[];
}

const props = defineProps<{
  mediaType: "film" | "series";
  tmdbId: number;
  season?: number | null;
  episode?: number | null;
  /** Si défini, aucun chargement API : la fiche parent fournit les données (série). */
  catalogueWork?: HiveLocalCatalogueWork | null;
}>();

const emit = defineEmits<{ changed: [] }>();

const loading = ref(false);
const error = ref("");
const loadedWork = ref<HiveLocalCatalogueWork | null>(null);

const usesParentWork = computed(() => props.catalogueWork !== undefined);

const work = computed(() => (usesParentWork.value ? props.catalogueWork ?? null : loadedWork.value));

const visible = computed(() => Boolean(work.value?.versions?.length));

async function loadFilm() {
  loading.value = true;
  error.value = "";
  try {
    const params = new URLSearchParams({
      tmdb_id: String(props.tmdbId),
      media_type: props.mediaType,
    });
    const page = await api<{ items: HiveLocalCatalogueWork[] }>(`/api/v1/hive/catalogue?${params.toString()}`);
    const items = page.items ?? [];
    let picked = items.find((row) => row.versions?.length) ?? items[0];
    if (picked?.work_key && !picked.versions?.length) {
      picked = await api<HiveLocalCatalogueWork>(`/api/v1/hive/catalogue/${encodeURIComponent(picked.work_key)}`);
    }
    loadedWork.value = picked?.versions?.length ? picked : null;
  } catch (err) {
    loadedWork.value = null;
    error.value = userFacingApiError(err, "Catalogue Hive indisponible.");
  } finally {
    loading.value = false;
  }
}

function refreshSelf() {
  if (usesParentWork.value) {
    emit("changed");
    return;
  }
  void loadFilm();
}

onMounted(() => {
  if (!usesParentWork.value) {
    void loadFilm();
  }
});

watch(
  () => [props.tmdbId, props.mediaType] as const,
  () => {
    if (!usesParentWork.value) {
      void loadFilm();
    }
  },
);

function onRequested() {
  refreshSelf();
}
</script>

<template>
  <article
    v-if="visible"
    class="panel hive-local-catalogue"
    data-testid="hive-local-catalogue-block"
    aria-labelledby="hive-local-catalogue-title"
  >
    <h2 id="hive-local-catalogue-title">Disponible dans la ruche</h2>
    <p v-if="!usesParentWork && loading" class="hint">Chargement des versions…</p>
    <p v-else-if="error" class="error" role="alert">{{ error }}</p>
    <HiveCatalogueVersionList
      v-else-if="work"
      :work-key="work.work_key"
      :versions="work.versions"
      :season="work.season"
      :episode="work.episode"
      compact
      @requested="onRequested"
    />
  </article>
</template>

<style scoped>
.hive-local-catalogue {
  display: grid;
  gap: 0.75rem;
}
</style>
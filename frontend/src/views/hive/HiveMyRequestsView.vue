<script setup lang="ts">
import { onMounted, ref } from "vue";

import HiveMyRequestList, { type DownloadProgressRow } from "../../components/hive/HiveMyRequestList.vue";
import { api, userFacingApiError } from "../../api";
import type { HiveMyRequestItem } from "../../hive/hiveRequests";

const items = ref<HiveMyRequestItem[]>([]);
const downloads = ref<DownloadProgressRow[]>([]);
const error = ref("");
const loading = ref(false);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const [mine, dl] = await Promise.all([
      api<{ items: HiveMyRequestItem[] }>("/api/v1/hive/requests/mine"),
      api<{ items?: DownloadProgressRow[]; downloads?: DownloadProgressRow[] }>("/api/v1/downloads").catch(
        () => ({ items: [] as DownloadProgressRow[], downloads: [] as DownloadProgressRow[] }),
      ),
    ]);
    items.value = mine.items ?? [];
    downloads.value = dl.items ?? dl.downloads ?? [];
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger vos demandes.");
    items.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(load);

defineExpose({ reload: load, itemCount: () => items.value.length });
</script>

<template>
  <section class="stack hive-mine-view">
    <p class="hint hive-mine-lead">
      Ce n’est pas une file séparée : ce sont vos suivis en attente déjà envoyés à la ruche (demande anonyme). La
      recherche habituelle, s’il y a une clé AllDebrid, a lieu d’abord.
    </p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="hint">Chargement…</p>
    <HiveMyRequestList v-else :items="items" :downloads="downloads" @changed="load" />
  </section>
</template>

<style scoped>
.hive-mine-lead {
  margin: 0 0 0.5rem;
  max-width: 52rem;
}
</style>
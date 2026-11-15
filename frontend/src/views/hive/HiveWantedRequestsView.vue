<script setup lang="ts">
import { onMounted, ref } from "vue";

import HiveWantedVolunteerList from "../../components/hive/HiveWantedVolunteerList.vue";
import { api, userFacingApiError } from "../../api";
import type { HiveWantedRequestItem } from "../../hive/hiveRequests";

const items = ref<HiveWantedRequestItem[]>([]);
const error = ref("");
const note = ref("");
const loading = ref(false);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const resp = await api<{ items: HiveWantedRequestItem[] }>("/api/v1/hive/requests/wanted");
    items.value = resp.items ?? [];
  } catch (err) {
    error.value = userFacingApiError(err, "Impossible de charger la liste Recherché.");
    items.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(load);

defineExpose({ reload: load, itemCount: () => items.value.length });
</script>

<template>
  <section class="stack hive-wanted-view">
    <p class="panel hive-wanted-info hint">
      Titres issus de suivis en attente, qu’aucun membre ne possède encore. « Je m’en occupe » suit le titre, le
      télécharge par la chaîne habituelle, puis le partage à l’arrivée. Plusieurs volontaires sont possibles.
    </p>
    <p v-if="note" class="ok">{{ note }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="loading" class="hint">Chargement…</p>
    <HiveWantedVolunteerList
      v-else
      :rows="items"
      @changed="load"
      @error="(msg) => (error = msg)"
      @note="(msg) => (note = msg)"
    />
  </section>
</template>

<style scoped>
.hive-wanted-info {
  line-height: 1.45;
}
</style>
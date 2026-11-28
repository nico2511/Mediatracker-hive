<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { api, userFacingApiError } from "../api";
import type { MetroActivityBody } from "../metro/types";
import MetroEdgePanel from "./metro/MetroEdgePanel.vue";

const route = useRoute();
const body = ref<MetroActivityBody | null>(null);
const error = ref("");

const edgeId = computed(() => String(route.params.edgeId ?? "").toUpperCase());

const edge = computed(() => body.value?.graph.edges.find((row) => row.id === edgeId.value) ?? null);

const graph = computed(() => body.value?.graph ?? null);

onMounted(async () => {
  try {
    body.value = await api<MetroActivityBody>("/api/v1/metro/activity?hours=24");
  } catch (err) {
    error.value = userFacingApiError(err, "Détail indisponible.");
  }
});
</script>

<template>
  <section class="stack metro-correspondence-page" data-testid="metro-correspondence-page">
    <header class="ph-crumbs">
      <RouterLink to="/sante" class="button-link">← Carte du réseau</RouterLink>
      <p class="hint">Réseau › Stations › {{ edgeId }}</p>
      <span class="lvl-chip">Niveau 2 · correspondance</span>
    </header>
    <p v-if="error" class="error">{{ error }}</p>
    <MetroEdgePanel
      v-else-if="edge && body"
      :edge="edge"
      :origin="graph?.stations.find((s) => s.id === edge.origin)"
      :dest="graph?.stations.find((s) => s.id === edge.dest)"
      :detail="(body.edge_details[edge.id] as any) ?? undefined"
    />
  </section>
</template>
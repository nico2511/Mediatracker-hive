<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { api, userFacingApiError } from "../api";
import type { MetroActivityBody, MetroGraphStation } from "../metro/types";
import MetroFrise from "./metro/MetroFrise.vue";

const route = useRoute();
const body = ref<MetroActivityBody | null>(null);
const error = ref("");

const stationId = computed(() => String(route.params.stationId ?? "").toUpperCase());

const station = computed((): MetroGraphStation | null => {
  return body.value?.graph.stations.find((row) => row.id === stationId.value) ?? null;
});

const outbound = computed(
  () => (body.value?.station_details[stationId.value]?.outbound as unknown[]) ?? [],
);

const edgeDetail = computed(() => {
  const edges = body.value?.edge_details ?? {};
  const first = outbound.value[0] as { edge_id?: string } | undefined;
  if (first?.edge_id && edges[first.edge_id]) {
    return edges[first.edge_id];
  }
  return edges.C16 ?? null;
});

onMounted(async () => {
  try {
    body.value = await api<MetroActivityBody>("/api/v1/metro/activity?hours=24");
  } catch (err) {
    error.value = userFacingApiError(err, "Station indisponible.");
  }
});
</script>

<template>
  <section class="stack metro-station-page" data-testid="metro-station-page">
    <header class="ph-crumbs">
      <RouterLink to="/sante" class="button-link">← Carte du réseau</RouterLink>
      <p class="hint">Réseau › Stations › {{ station?.label ?? stationId }}</p>
      <span class="lvl-chip">Niveau 2 · station</span>
    </header>
    <p v-if="error" class="error">{{ error }}</p>
    <article v-else-if="station && body" class="panel stack">
      <h1>{{ station.label }}</h1>
      <dl class="kv">
        <dt>Passages / 24 h</dt>
        <dd>{{ body.station_stats[stationId]?.passes_24h ?? 0 }}</dd>
      </dl>
      <h2>Correspondances en sortie</h2>
      <ul>
        <li v-for="row in outbound" :key="(row as any).edge_id">
          {{ (row as any).dest_line }} · {{ (row as any).dest_label }}
          ({{ (row as any).passes_24h }})
        </li>
      </ul>
      <MetroFrise
        v-if="(edgeDetail as any)?.timeline_24h"
        label="24 h"
        :buckets="(edgeDetail as any).timeline_24h"
        :zoom-buckets="(edgeDetail as any).timeline_3h"
      />
      <h3>Passages récents</h3>
      <table class="tbl">
        <tbody>
          <tr v-for="(row, idx) in (edgeDetail as any)?.passages_recent ?? []" :key="idx">
            <td>{{ row.time }}</td>
            <td>{{ row.subject_ref }}</td>
          </tr>
        </tbody>
      </table>
      <h3>Cascade en aval</h3>
      <div class="casc">
        <div
          v-for="step in (edgeDetail as any)?.cascade ?? []"
          :key="step.station_id"
          class="casc-step"
        >
          <span class="d" />
          <div>
            <strong>{{ step.label }}</strong>
            <span class="t">{{ step.line }}</span>
          </div>
        </div>
      </div>
      <p class="note-foot">Lecture seule.</p>
    </article>
  </section>
</template>
<script setup lang="ts">
import type { MetroGraphEdge, MetroGraphStation } from "../../metro/types";
import MetroFrise from "./MetroFrise.vue";

export type EdgeDetail = {
  edge_id: string;
  timeline_24h: { at: string; passes: number; blocked: number; median_ms?: number | null }[];
  timeline_3h: { at: string; passes: number; blocked: number; median_ms?: number | null }[];
  passages_recent: {
    time: string;
    subject_ref: string;
    effect: string;
    duration_min: number | null;
  }[];
  cascade: { station_id: string; line: string; label: string }[];
  block_minutes?: number;
};

const props = defineProps<{
  edge: MetroGraphEdge;
  origin: MetroGraphStation | undefined;
  dest: MetroGraphStation | undefined;
  detail: EdgeDetail | undefined;
}>();
</script>

<template>
  <aside class="side panel metro-edge-panel" data-testid="metro-edge-panel">
    <p class="kick">Correspondance {{ edge.id }} · {{ edge.kind }} · niveau 2</p>
    <h2>{{ origin?.label }} → {{ dest?.label }}</h2>
    <div v-if="detail?.block_minutes" class="chips">
      <span class="chip danger">Blocage en cours depuis {{ detail.block_minutes }} min</span>
    </div>
    <MetroFrise
      v-if="detail?.timeline_24h?.length"
      label="24 h"
      :buckets="detail.timeline_24h"
      :zoom-buckets="detail.timeline_3h"
    />
    <h3>Passages récents</h3>
    <table class="tbl">
      <thead>
        <tr>
          <th>Heure</th>
          <th>Rame</th>
          <th class="r">Durée</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, index) in detail?.passages_recent ?? []" :key="index">
          <td>{{ row.time }}</td>
          <td>
            {{ row.subject_ref }}
            <span class="hint">{{ row.effect }}</span>
          </td>
          <td class="r">{{ row.duration_min != null ? `${row.duration_min} min` : "—" }}</td>
        </tr>
      </tbody>
    </table>
    <h3>Cascade en aval</h3>
    <div class="casc">
      <div v-for="step in detail?.cascade ?? []" :key="step.station_id" class="casc-step">
        <span class="d" :style="{ borderColor: '#cfd2d0' }" />
        <div>
          <strong>{{ step.label }}</strong>
          <span class="t">{{ step.line }}</span>
        </div>
      </div>
    </div>
    <p v-if="edge.proof" class="callout danger code">{{ edge.proof }}</p>
    <p class="note-foot">Lecture seule : aucune action sur les tâches.</p>
  </aside>
</template>
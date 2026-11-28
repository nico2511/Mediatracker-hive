<script setup lang="ts">
import type { MetroGraphStation } from "../../metro/types";

export type OutboundRow = {
  edge_id: string;
  kind: string;
  dest_label: string;
  dest_line: string;
  passes_24h: number;
  lit: boolean;
  untraceable?: boolean;
};

defineProps<{
  station: MetroGraphStation;
  stats: {
    passes_24h: number;
    median_ms: number | null;
    last_subject_ref: string | null;
  };
  outbound: OutboundRow[];
}>();

defineEmits<{
  enter: [];
}>();
</script>

<template>
  <aside class="side panel" data-testid="metro-station-panel">
    <p class="kick">Station de correspondance · niveau 2</p>
    <h2>{{ station.label }}</h2>
    <div class="chips">
      <span class="chip">
        <i :style="{ background: station.color }" />
        {{ station.line_number }} {{ station.line }}
      </span>
      <span class="chip muted">{{ outbound.length }} lignes en sortie</span>
    </div>
    <dl class="kv">
      <dt>Passages / 24 h</dt>
      <dd>
        {{ stats.passes_24h }} · médiane
        {{ stats.median_ms != null ? `${(stats.median_ms / 1000).toFixed(1)} s` : "—" }}
      </dd>
      <dt v-if="stats.last_subject_ref">Dernier passage</dt>
      <dd v-if="stats.last_subject_ref">{{ stats.last_subject_ref }}</dd>
    </dl>
    <h3>Correspondances en sortie</h3>
    <div class="links">
      <div
        v-for="row in outbound"
        :key="row.edge_id"
        class="link-row"
        :class="{ lit: row.lit, bad: row.kind === 'bloque' }"
      >
        <span>{{ row.dest_line }} · {{ row.dest_label }}</span>
        <span class="n">{{ row.untraceable ? "?" : row.passes_24h }}</span>
      </div>
    </div>
    <div v-if="station.id === 'RNG5'" class="btn-row">
      <button type="button" class="button" data-testid="metro-enter-station" @click="$emit('enter')">
        Entrer dans la station
      </button>
    </div>
    <p class="note-foot">Lecture seule : aucune action sur les tâches.</p>
  </aside>
</template>
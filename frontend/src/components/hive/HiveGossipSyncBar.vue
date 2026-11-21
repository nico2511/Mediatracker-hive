<script setup lang="ts">
import { computed } from "vue";

import { hiveGossipCountdownLabel, hiveGossipProgressFraction } from "../../hive/hiveGossipSync";

const props = defineProps<{
  nextMs: number | null;
  lastMs: number | null;
  nowMs: number;
}>();

const visible = computed(() => props.nextMs != null);

const countdown = computed(() => {
  if (props.nextMs == null) {
    return "";
  }
  return hiveGossipCountdownLabel(props.nextMs - props.nowMs);
});

const progressPct = computed(() => {
  const frac = hiveGossipProgressFraction(props.nowMs, props.nextMs, props.lastMs);
  return `${Math.round(frac * 1000) / 10}%`;
});
</script>

<template>
  <div v-if="visible" class="hive-gossip-sync panel" data-testid="hive-gossip-sync" role="status">
    <div class="hive-gossip-sync-head row sp">
      <span class="hive-gossip-sync-label">Prochaine mise à jour de la ruche</span>
      <span class="hive-gossip-sync-countdown hint" data-testid="hive-gossip-countdown">{{ countdown }}</span>
    </div>
    <div class="hive-gossip-sync-track" aria-hidden="true">
      <span class="hive-gossip-sync-fill" :style="{ width: progressPct }" />
    </div>
  </div>
</template>

<style scoped>
.hive-gossip-sync {
  margin-bottom: 0.85rem;
  padding: 0.65rem 0.85rem;
  border-color: color-mix(in srgb, var(--honey, #d9b46a) 35%, var(--border));
  background: color-mix(in srgb, var(--honey, #d9b46a) 6%, var(--card));
}

.hive-gossip-sync-label {
  font-weight: 600;
  font-size: 0.92rem;
}

.hive-gossip-sync-countdown {
  font-size: 0.88rem;
  white-space: nowrap;
}

.hive-gossip-sync-track {
  margin-top: 0.5rem;
  height: 4px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--text) 12%, transparent);
  overflow: hidden;
}

.hive-gossip-sync-fill {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: color-mix(in srgb, var(--honey, #d9b46a) 75%, var(--text));
  transition: width 0.4s ease;
}
</style>
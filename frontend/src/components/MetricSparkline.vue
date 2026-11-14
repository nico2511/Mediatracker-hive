<script setup lang="ts">
import { computed } from "vue";

const props = withDefaults(
  defineProps<{
    values: (number | null)[];
    height?: number;
    unit?: string;
    formatValue?: (value: number) => string;
  }>(),
  {
    height: 56,
    unit: "",
    formatValue: (value: number) => String(Math.round(value * 10) / 10),
  },
);

const numeric = computed(() => props.values.filter((value): value is number => value != null && Number.isFinite(value)));

const current = computed(() => {
  const rows = numeric.value;
  if (!rows.length) {
    return null;
  }
  return rows[rows.length - 1];
});

const min = computed(() => (numeric.value.length ? Math.min(...numeric.value) : null));
const max = computed(() => (numeric.value.length ? Math.max(...numeric.value) : null));

const path = computed(() => {
  const rows = props.values;
  if (rows.length < 2) {
    return "";
  }
  const width = 200;
  const height = props.height;
  const padding = 4;
  const valid = rows.map((value) => (value == null || !Number.isFinite(value) ? null : value));
  const present = valid.filter((value): value is number => value != null);
  if (present.length < 2) {
    return "";
  }
  const low = Math.min(...present);
  const high = Math.max(...present);
  const span = high - low || 1;
  const step = width / (rows.length - 1);
  const points: string[] = [];
  rows.forEach((value, index) => {
    if (value == null || !Number.isFinite(value)) {
      return;
    }
    const x = index * step;
    const y = padding + (height - padding * 2) * (1 - (value - low) / span);
    points.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  return points.length >= 2 ? `M ${points.join(" L ")}` : "";
});
</script>

<template>
  <figure class="metric-sparkline">
    <svg
      class="metric-sparkline-chart"
      viewBox="0 0 200 56"
      role="img"
      :aria-label="current != null ? `Valeur actuelle ${formatValue(current)}${unit}` : 'Historique vide'"
    >
      <line x1="0" y1="52" x2="200" y2="52" class="metric-sparkline-axis" />
      <path v-if="path" :d="path" class="metric-sparkline-line" fill="none" />
    </svg>
    <figcaption v-if="numeric.length" class="metric-sparkline-scale hint">
      <span>min {{ min != null ? formatValue(min) : "—" }}{{ unit }}</span>
      <span v-if="current != null">actuel {{ formatValue(current) }}{{ unit }}</span>
      <span>max {{ max != null ? formatValue(max) : "—" }}{{ unit }}</span>
    </figcaption>
  </figure>
</template>

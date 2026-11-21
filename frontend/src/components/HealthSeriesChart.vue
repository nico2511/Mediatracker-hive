<script setup lang="ts">
import { computed } from "vue";

const props = defineProps<{
  label: string;
  unit: string;
  points: { ts: number; value: number }[];
  height?: number;
}>();

const width = 320;
const chartHeight = computed(() => props.height ?? 120);
const padding = { top: 8, right: 8, bottom: 28, left: 44 };

const values = computed(() => props.points.map((point) => point.value));

const min = computed(() => (values.value.length ? Math.min(...values.value) : 0));
const max = computed(() => (values.value.length ? Math.max(...values.value) : 1));

const yTicks = computed(() => {
  const low = min.value;
  const high = max.value;
  const span = high - low || 1;
  const mid = low + span / 2;
  return [high, mid, low];
});

const path = computed(() => {
  const rows = props.points;
  if (rows.length < 2) {
    return "";
  }
  const innerW = width - padding.left - padding.right;
  const innerH = chartHeight.value - padding.top - padding.bottom;
  const low = min.value;
  const high = max.value;
  const span = high - low || 1;
  const start = rows[0].ts;
  const end = rows[rows.length - 1].ts;
  const timeSpan = end - start || 1;
  const coords = rows.map((point) => {
    const x = padding.left + ((point.ts - start) / timeSpan) * innerW;
    const y = padding.top + innerH * (1 - (point.value - low) / span);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return `M ${coords.join(" L ")}`;
});

function formatValue(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded}`;
}

function formatDay(ts: number): string {
  return new Date(ts * 1000).toLocaleDateString("fr-FR", { day: "2-digit", month: "short" });
}

const xLabels = computed(() => {
  const rows = props.points;
  if (!rows.length) {
    return [] as { x: number; label: string }[];
  }
  const innerW = width - padding.left - padding.right;
  const start = rows[0].ts;
  const end = rows[rows.length - 1].ts;
  const timeSpan = end - start || 1;
  const picks = [0, 0.5, 1];
  return picks.map((ratio) => {
    const ts = start + timeSpan * ratio;
    const x = padding.left + ratio * innerW;
    return { x, label: formatDay(ts) };
  });
});

function yFor(value: number): number {
  const innerH = chartHeight.value - padding.top - padding.bottom;
  const low = min.value;
  const high = max.value;
  const span = high - low || 1;
  return padding.top + innerH * (1 - (value - low) / span);
}
</script>

<template>
  <figure class="health-series-chart">
    <figcaption>{{ label }}</figcaption>
    <svg
      :viewBox="`0 0 ${width} ${chartHeight}`"
      class="health-series-chart-svg"
      role="img"
      :aria-label="`${label} sur 7 jours`"
    >
      <line
        :x1="padding.left"
        :y1="chartHeight - padding.bottom"
        :x2="width - padding.right"
        :y2="chartHeight - padding.bottom"
        class="health-series-axis"
      />
      <line
        :x1="padding.left"
        :y1="padding.top"
        :x2="padding.left"
        :y2="chartHeight - padding.bottom"
        class="health-series-axis"
      />
      <g v-for="tick in yTicks" :key="tick">
        <line
          :x1="padding.left"
          :x2="width - padding.right"
          :y1="yFor(tick)"
          :y2="yFor(tick)"
          class="health-series-grid"
        />
        <text :x="4" :y="yFor(tick) + 4" class="health-series-label">{{ formatValue(tick) }}{{ unit }}</text>
      </g>
      <g v-for="(mark, index) in xLabels" :key="index">
        <text
          :x="mark.x"
          :y="chartHeight - 6"
          :text-anchor="index === 0 ? 'start' : index === xLabels.length - 1 ? 'end' : 'middle'"
          class="health-series-label"
        >
          {{ mark.label }}
        </text>
      </g>
      <path v-if="path" :d="path" class="health-series-line" fill="none" />
    </svg>
    <p v-if="!points.length" class="hint">Pas encore de mesures sur cette période.</p>
  </figure>
</template>

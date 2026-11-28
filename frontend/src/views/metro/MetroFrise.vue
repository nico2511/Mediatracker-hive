<script setup lang="ts">
import { computed, ref } from "vue";

export type FriseBucket = {
  at: string;
  passes: number;
  blocked: number;
  median_ms?: number | null;
};

const props = defineProps<{
  label: string;
  buckets: FriseBucket[];
  zoomBuckets?: FriseBucket[];
}>();

const active = ref<{ row: FriseBucket; zoom: boolean } | null>(null);

const maxCount = computed(() =>
  Math.max(1, ...props.buckets.map((row) => row.passes + row.blocked)),
);

const labels = computed(() => {
  if (!props.buckets.length) {
    return [];
  }
  const first = props.buckets[0]!.at;
  const last = props.buckets[props.buckets.length - 1]!.at;
  const mid = props.buckets[Math.floor(props.buckets.length / 2)]!.at;
  const fmt = (iso: string) =>
    new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return [fmt(first), fmt(mid), fmt(last)];
});

function formatTip(row: FriseBucket): string {
  const time = new Date(row.at).toLocaleTimeString("fr-FR", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const count = row.passes + row.blocked;
  const median =
    row.median_ms != null && row.median_ms > 0
      ? `${(row.median_ms / 1000).toFixed(1)} s`
      : "—";
  return `${time} · ${count} passage${count > 1 ? "s" : ""} · médiane ${median}`;
}

function showTip(row: FriseBucket, zoom: boolean) {
  active.value = { row, zoom };
}

function hideTip() {
  active.value = null;
}

function toggleTip(row: FriseBucket, zoom: boolean) {
  if (active.value?.row === row && active.value.zoom === zoom) {
    active.value = null;
    return;
  }
  showTip(row, zoom);
}
</script>

<template>
  <section class="metro-frise-block">
    <div class="fr-lbl">
      <span>{{ label }}</span>
      <span v-if="zoomBuckets">cadre : 3 h</span>
    </div>
    <div class="frise" role="group" :aria-label="label">
      <button
        v-for="(bucket, index) in buckets"
        :key="index"
        type="button"
        class="b frise-slot"
        :class="{ blk: bucket.blocked > 0, on: active?.row === bucket && !active?.zoom }"
        :style="{
          left: `${(index / buckets.length) * 100}%`,
          width: `${100 / buckets.length}%`,
          opacity: 0.35 + (0.65 * (bucket.passes + bucket.blocked)) / maxCount,
        }"
        :title="formatTip(bucket)"
        :aria-label="formatTip(bucket)"
        @mouseenter="showTip(bucket, false)"
        @mouseleave="hideTip"
        @click="toggleTip(bucket, false)"
      />
    </div>
    <p v-if="active && !active.zoom" class="frise-tip" role="status">{{ formatTip(active.row) }}</p>
    <div class="frise-axis">
      <span v-for="(labelText, index) in labels" :key="index">{{ labelText }}</span>
    </div>
    <template v-if="zoomBuckets?.length">
      <div class="fr-lbl">
        <span>Zoom 3 h</span>
        <span>trait : une passe</span>
      </div>
      <div class="frise frise-zoom" role="group" aria-label="Zoom 3 h">
        <button
          v-for="(bucket, index) in zoomBuckets"
          :key="`z-${index}`"
          type="button"
          class="b frise-slot"
          :class="{ blk: bucket.blocked > 0, on: active?.row === bucket && active?.zoom }"
          :style="{
            left: `${(index / zoomBuckets.length) * 100}%`,
            width: `${100 / zoomBuckets.length}%`,
            opacity:
              0.4 +
              (0.6 * (bucket.passes + bucket.blocked)) /
                Math.max(1, ...zoomBuckets.map((b) => b.passes + b.blocked)),
          }"
          :title="formatTip(bucket)"
          :aria-label="formatTip(bucket)"
          @mouseenter="showTip(bucket, true)"
          @mouseleave="hideTip"
          @click="toggleTip(bucket, true)"
        />
      </div>
      <p v-if="active && active.zoom" class="frise-tip" role="status">{{ formatTip(active.row) }}</p>
    </template>
  </section>
</template>
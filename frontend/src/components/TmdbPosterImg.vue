<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { tmdbPosterDims, tmdbPosterSrc, tmdbPosterSrcSet } from "../tmdbImages";

const props = withDefaults(
  defineProps<{
    url: string | null | undefined;
    alt?: string;
    thumb?: boolean;
    lazy?: boolean;
    fallback?: string;
    offlineGhost?: boolean;
  }>(),
  { alt: "", thumb: false, lazy: true, fallback: "", offlineGhost: false },
);

const failed = ref(false);

watch(
  () => props.url,
  () => {
    failed.value = false;
  },
);

const size = computed(() => (props.thumb ? "w92" : "w342"));
const src = computed(() => tmdbPosterSrc(props.url, size.value));
const srcset = computed(() => tmdbPosterSrcSet(props.url, props.thumb));
const dims = computed(() => tmdbPosterDims(size.value));
const showImage = computed(() => Boolean(src.value) && !failed.value && !props.offlineGhost);
const showFallback = computed(() => Boolean(props.fallback) && !showImage.value && !props.offlineGhost);
</script>

<template>
  <span v-if="offlineGhost" class="poster-ghost" aria-hidden="true"><i>{{ fallback || "Film" }}</i></span>
  <img
    v-else-if="showImage"
    :src="src!"
    :srcset="srcset"
    :width="dims.width"
    :height="dims.height"
    :alt="alt"
    :loading="lazy ? 'lazy' : undefined"
    decoding="async"
    @error="failed = true"
  />
  <span v-else-if="showFallback" class="poster-fallback">{{ fallback }}</span>
</template>

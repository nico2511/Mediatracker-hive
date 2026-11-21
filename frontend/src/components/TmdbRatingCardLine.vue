<script setup lang="ts">
import { computed } from "vue";

import { tmdbRatingLabel } from "../tmdbRatingLabel";

const props = defineProps<{
  voteAverage?: number | null;
}>();

const score = computed(() => tmdbRatingLabel(props.voteAverage ?? null));
</script>

<template>
  <span class="tmdb-rating-card-line hint" :class="{ 'tmdb-rating-card-line--placeholder': !score }">
    <template v-if="score">
      <span class="visually-hidden">Note TMDB {{ score }} sur 10</span>
      <span class="tmdb-rating-card-visible" aria-hidden="true">
        <span class="tmdb-rating-star">★</span>{{ " " }}{{ score }}
      </span>
    </template>
  </span>
</template>
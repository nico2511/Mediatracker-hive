<script setup lang="ts">
import { computed } from "vue";

import { hiddenHomeItemCount, visibleHomeItemCount } from "../homeSectionLimits";

const props = withDefaults(
  defineProps<{
    title: string;
    hint?: string;
    count?: number;
    itemsLength: number;
    expanded: boolean;
    collapsedDesktop?: number;
    collapsedMobile?: number;
    wide: boolean;
    sectionId?: string;
  }>(),
  {
    collapsedDesktop: 7,
    collapsedMobile: 6,
  },
);

const emit = defineEmits<{
  toggle: [];
}>();

const visibleCount = computed(() =>
  visibleHomeItemCount(
    props.itemsLength,
    props.expanded,
    props.wide,
    props.collapsedDesktop,
    props.collapsedMobile,
  ),
);

const hiddenCount = computed(() =>
  hiddenHomeItemCount(
    props.itemsLength,
    props.expanded,
    props.wide,
    props.collapsedDesktop,
    props.collapsedMobile,
  ),
);

const showToggle = computed(() => props.expanded || hiddenCount.value > 0);
</script>

<template>
  <article :id="sectionId" class="panel home-section">
    <header class="home-section-header">
      <div class="home-section-titles">
        <h2>{{ title }}</h2>
        <p v-if="hint || count != null" class="hint home-section-hint">
          <template v-if="count != null">{{ count }}</template>
          <template v-if="hint">
            <template v-if="count != null"> · </template>
            {{ hint }}
          </template>
        </p>
      </div>
      <div class="home-section-actions">
        <slot name="actions" />
      </div>
    </header>
    <slot :limit="visibleCount" />
    <p v-if="showToggle" class="home-section-more">
      <button type="button" class="link-button" @click="emit('toggle')">
        {{ expanded ? "Voir moins" : `Voir plus (${hiddenCount})` }}
      </button>
    </p>
  </article>
</template>

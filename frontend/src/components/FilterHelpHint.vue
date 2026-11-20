<script setup lang="ts">
import { ref } from "vue";

defineProps<{
  label: string;
  text: string;
}>();

const open = ref(false);

function show() {
  open.value = true;
}

function hide() {
  open.value = false;
}

function toggle() {
  open.value = !open.value;
}
</script>

<template>
  <span
    class="filter-help-hint"
    @mouseenter="show"
    @mouseleave="hide"
    @focusin="show"
    @focusout="hide"
  >
    <button
      type="button"
      class="filter-help-hint-btn"
      :aria-label="`Aide : ${label}`"
      :aria-expanded="open ? 'true' : 'false'"
      @click="toggle"
    >
      ?
    </button>
    <span v-if="open" class="filter-help-hint-tip file-toggle-tip tip" role="tooltip">{{ text }}</span>
  </span>
</template>
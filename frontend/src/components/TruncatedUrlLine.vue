<script setup lang="ts">
import { computed, ref } from "vue";

const props = defineProps<{
  url: string;
}>();

const expanded = ref(false);

const displayText = computed(() => props.url.trim());

function toggleExpanded() {
  expanded.value = !expanded.value;
}

async function copyUrl() {
  const text = displayText.value;
  if (!text) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    /* copie refusée */
  }
}
</script>

<template>
  <div class="url-ellipsis-line" :class="{ 'url-ellipsis-line--open': expanded }">
    <code :title="displayText">{{ displayText }}</code>
    <button type="button" class="url-ellipsis-action" @click="toggleExpanded">
      {{ expanded ? "Réduire" : "Voir" }}
    </button>
    <button type="button" class="url-ellipsis-action" @click="copyUrl">Copier</button>
  </div>
</template>
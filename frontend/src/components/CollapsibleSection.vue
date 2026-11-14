<script setup lang="ts">
withDefaults(
  defineProps<{
    title: string;
    /** Ouvert au premier affichage (attribut HTML `open` sur `<details>`). */
    defaultOpen?: boolean;
  }>(),
  { defaultOpen: false },
);
</script>

<template>
  <details class="panel collapsible-section" :open="defaultOpen || undefined">
    <summary class="collapsible-section-summary">
      <span class="collapsible-section-chevron" aria-hidden="true" />
      <span class="collapsible-section-title">{{ title }}</span>
    </summary>
    <div class="collapsible-section-body stack">
      <slot />
    </div>
  </details>
</template>

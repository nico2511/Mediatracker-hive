<script setup lang="ts">
import ActionMenuPopover from "./ActionMenuPopover.vue";

withDefaults(
  defineProps<{
    triggerVariant?: "toggle" | "line" | "fiche";
    compactOnPhone?: boolean;
    menuAriaLabel?: string;
  }>(),
  {
    triggerVariant: "line",
    compactOnPhone: false,
    menuAriaLabel: "Plus d'actions",
  },
);
</script>

<template>
  <ActionMenuPopover
    class="line-more-actions"
    label="Plus d'actions"
    :menu-aria-label="menuAriaLabel"
    :trigger-variant="triggerVariant"
    :compact-on-phone="compactOnPhone"
    :show-status-icon="triggerVariant === 'line'"
  >
    <slot />
  </ActionMenuPopover>
</template>

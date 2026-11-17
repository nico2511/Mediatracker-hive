<script setup lang="ts">
defineOptions({ inheritAttrs: false });

withDefaults(
  defineProps<{
    title: string;
    configured: boolean;
    statusTestId?: string;
    configuredLabel?: string;
    notConfiguredLabel?: string;
  }>(),
  {
    configuredLabel: "Enregistré",
    notConfiguredLabel: "Non renseigné",
  },
);
</script>

<template>
  <article class="panel stack settings-service-card" v-bind="$attrs">
    <header class="settings-service-card-head">
      <div class="settings-service-card-title-row">
        <h3 class="settings-subsection-title settings-service-card-title">{{ title }}</h3>
        <span
          class="badge settings-service-card-status"
          :class="{ on: configured }"
          :data-testid="statusTestId"
        >
          {{ configured ? configuredLabel : notConfiguredLabel }}
        </span>
      </div>
      <div v-if="$slots.docs" class="settings-service-card-docs">
        <slot name="docs" />
      </div>
    </header>
    <slot />
  </article>
</template>
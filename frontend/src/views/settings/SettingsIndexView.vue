<script setup lang="ts">
import { RouterLink } from "vue-router";

import { SETTINGS_SECTIONS } from "./sections";

const sectionPath = (slug: string) => `/reglages/${slug}`;
</script>

<template>
  <div class="settings-index zone">
    <RouterLink
      v-for="row in SETTINGS_SECTIONS"
      :key="row.slug"
      class="settings-rowlink"
      :to="sectionPath(row.slug)"
    >
      <span>
        <strong>{{ row.title }}</strong>
      </span>
      <span class="settings-chev" aria-hidden="true" />
    </RouterLink>
  </div>
</template>

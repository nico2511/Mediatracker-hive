<script setup lang="ts">
import { RouterLink } from "vue-router";

import { useServiceStatus } from "../../composables/useServiceStatus";
import { settingsIndexBadgeLabel } from "./settingsIndexBadge";
import { settingsSectionsByGroup } from "./sections";

const groups = settingsSectionsByGroup();
const status = useServiceStatus();

const sectionPath = (slug: string) => `/reglages/${slug}`;

const badgeFor = (slug: string) => settingsIndexBadgeLabel(slug, status);
</script>

<template>
  <div class="settings-index zone">
    <div v-for="group in groups" :key="group.id" class="settings-index-group">
      <h2 class="settings-subsection-title settings-index-group-title">
        {{ group.title }}
      </h2>
      <RouterLink
        v-for="row in group.sections"
        :key="row.slug"
        class="settings-rowlink"
        :to="sectionPath(row.slug)"
        :data-testid="`settings-index-link-${row.slug}`"
      >
        <span class="settings-rowlink-label">
          <strong>{{ row.title }}</strong>
          <span
            v-if="badgeFor(row.slug)"
            class="badge settings-index-badge"
            :data-testid="`settings-index-badge-${row.slug}`"
          >
            {{ badgeFor(row.slug) }}
          </span>
        </span>
        <span class="settings-chev" aria-hidden="true" />
      </RouterLink>
    </div>
  </div>
</template>
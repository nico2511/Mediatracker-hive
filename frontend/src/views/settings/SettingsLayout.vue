<script setup lang="ts">
import { computed } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { settingsSectionsByGroup } from "./sections";

const navGroups = settingsSectionsByGroup();

const route = useRoute();

const needsHttpsWarning = computed(() => {
  if (typeof window === "undefined") {
    return false;
  }
  const { protocol, hostname } = window.location;
  if (protocol === "https:") {
    return false;
  }
  return hostname !== "localhost" && hostname !== "127.0.0.1";
});

const onIndex = computed(() => route.name === "settings-index");
const activeSlug = computed(() => {
  if (route.name === "settings-hive") {
    return "hive";
  }
  if (route.name === "settings-sources") {
    return "sources";
  }
  const raw = route.params.section;
  return typeof raw === "string" ? raw : "";
});
const sectionPath = (slug: string) => `/reglages/${slug}`;
</script>

<template>
  <div class="settings-page">
    <p v-if="needsHttpsWarning" class="settings-https-warn" role="status">
      Le mode hors ligne et l'installation nécessitent une adresse en HTTPS
    </p>
    <header class="settings-head">
      <RouterLink
        v-if="!onIndex"
        class="settings-back phone-only"
        :to="{ name: 'settings-index' }"
        aria-label="Retour au sommaire des réglages"
      >
        ← Réglages
      </RouterLink>
      <h1>Réglages</h1>
    </header>

    <nav
      v-if="!onIndex"
      class="settings-tabs desk-only"
      aria-label="Sections des réglages"
    >
      <template v-for="group in navGroups" :key="group.id">
        <p class="settings-tabs-group-title settings-subsection-title">
          {{ group.title }}
        </p>
        <RouterLink
          v-for="item in group.sections"
          :key="item.slug"
          :to="sectionPath(item.slug)"
          :aria-current="item.slug === activeSlug ? 'page' : undefined"
        >
          {{ item.title }}
        </RouterLink>
      </template>
    </nav>

    <RouterView />
  </div>
</template>

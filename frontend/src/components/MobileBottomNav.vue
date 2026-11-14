<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";

import SidebarIcon from "./SidebarIcon.vue";
import { useConnectivity } from "../pwa/connectivity";
import { useHiveEnabled } from "../composables/useHive";

const route = useRoute();
const router = useRouter();
const { queueCount } = useConnectivity();
const { hiveEnabled } = useHiveEnabled();

type TabDef = {
  key: string;
  label: string;
  to?: string;
  icon: string;
  action?: "menu";
};

const tabs = computed((): TabDef[] => {
  const compact = hiveEnabled.value;
  const base: TabDef[] = [
    { key: "home", label: compact ? "Acc." : "Accueil", to: "/", icon: "home" },
    { key: "follow", label: "Suivre", to: "/suivre", icon: "follow" },
    { key: "media", label: "Médias", to: "/medias", icon: "media" },
    { key: "downloads", label: compact ? "Tél." : "Téléch.", to: "/telechargements", icon: "download" },
  ];
  if (hiveEnabled.value) {
    base.push({ key: "hive", label: "Hive", to: "/hive/catalogue", icon: "hive" });
  }
  base.push({ key: "more", label: "Plus", icon: "settings", action: "menu" });
  return base;
});

const sixTabs = computed(() => tabs.value.length >= 6);

const badge = computed(() => (queueCount.value > 0 ? `+${queueCount.value}` : ""));

function isActive(path: string): boolean {
  if (path === "/") {
    return route.path === "/";
  }
  return route.path === path || route.path.startsWith(`${path}/`);
}

const emit = defineEmits<{ menu: [] }>();

function onTab(tab: TabDef) {
  if (tab.action === "menu") {
    emit("menu");
    return;
  }
  void router.push(tab.to!);
}
</script>

<template>
  <nav class="mobile-tabbar" :class="{ 'mobile-tabbar--six': sixTabs }" aria-label="Navigation principale">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      class="mobile-tab"
      :class="{ active: tab.to && isActive(tab.to) }"
      @click="onTab(tab)"
    >
      <span class="mobile-tab-icon">
        <SidebarIcon :name="tab.icon" />
        <span v-if="tab.key === 'follow' && badge" class="mobile-tab-badge">{{ badge }}</span>
      </span>
      <span class="mobile-tab-label">{{ tab.label }}</span>
    </button>
  </nav>
</template>

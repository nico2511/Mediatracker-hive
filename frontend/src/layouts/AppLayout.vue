<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import ConnectivityBar from "../components/ConnectivityBar.vue";
import MobileBottomNav from "../components/MobileBottomNav.vue";
import SidebarIcon from "../components/SidebarIcon.vue";
import { api, type User } from "../api";
import { fetchHealthSummary, fetchJellyfinWeb } from "../layoutCache";
import { clearSessionAndOfflineData, sessionGate } from "../session";
import { useHiveEnabled } from "../composables/useHive";

interface NavItem {
  key: string;
  label: string;
  to?: string;
  href?: string;
  icon: string;
  external?: boolean;
  action?: "logout";
  heading?: boolean;
}

const STORAGE_KEY = "mt.sidebarCollapsed";

const router = useRouter();
const route = useRoute();
const user = ref<User | null>(null);
const versionLabel = ref("");
const versionTitle = ref("");
const menuOpen = ref(false);
const collapsed = ref(false);
const jellyfinHome = ref<string | null>(null);
const { hiveEnabled, refreshHive } = useHiveEnabled();
const hideMobileNav = computed(() => Boolean(route.meta.immersive));

const navItems = computed((): NavItem[] => {
  const items: NavItem[] = [
    { key: "home", label: "Accueil", to: "/", icon: "home" },
    { key: "follow", label: "Suivre", to: "/suivre", icon: "follow" },
    { key: "media", label: "Médias", to: "/medias", icon: "media" },
    { key: "news-films", label: "Derniers films", to: "/nouveautes/films", icon: "star" as const },
    { key: "news-series", label: "Dernières séries", to: "/nouveautes/series", icon: "star" as const },
    ...(hiveEnabled.value
      ? [{ key: "hive", label: "Hive", to: "/hive/catalogue", icon: "hive" as const }]
      : []),
    { key: "downloads", label: "Téléchargements", to: "/telechargements", icon: "download" },
    { key: "library", label: "Bibliothèque", to: "/bibliotheque", icon: "library" },
    { key: "journal", label: "Journal", to: "/journal", icon: "journal" },
  ];
  if (jellyfinHome.value) {
    items.push({
      key: "jellyfin",
      label: "Jellyfin",
      href: jellyfinHome.value,
      icon: "jellyfin",
      external: true,
    });
  }
  items.push(
    { key: "settings", label: "Réglages", to: "/reglages", icon: "settings" },
    { key: "health", label: "Santé", to: "/sante", icon: "health" },
    { key: "logout", label: "Déconnexion", icon: "logout", action: "logout" },
  );
  return items;
});

watch(
  () => route.path,
  () => {
    menuOpen.value = false;
  },
);

onMounted(async () => {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    collapsed.value = stored === "1";
  } catch {
    collapsed.value = false;
  }
  user.value = (await sessionGate()).user;
  try {
    const web = await fetchJellyfinWeb();
    jellyfinHome.value = web.url;
  } catch {
    jellyfinHome.value = null;
  }
  await refreshHive();
  try {
    const health = await fetchHealthSummary();
    const parts = [health.version, health.sha].filter((part): part is string => Boolean(part));
    versionLabel.value = parts.join(" · ");
    versionTitle.value = [health.version, health.sha, health.build_date].filter((part): part is string => Boolean(part)).join(" · ");
  } catch {
    versionLabel.value = "";
  }
});

function toggleCollapsed() {
  collapsed.value = !collapsed.value;
  try {
    localStorage.setItem(STORAGE_KEY, collapsed.value ? "1" : "0");
  } catch {
    // ignore
  }
}

async function logout() {
  await api("/api/v1/auth/logout", { method: "POST" });
  clearSessionAndOfflineData();
  await router.push("/login");
}

async function onNavAction(item: NavItem) {
  if (item.action === "logout") {
    await logout();
  }
}
</script>

<template>
  <div class="shell" :class="{ 'sidebar-collapsed': collapsed }">
    <aside class="sidebar" :class="{ 'menu-open': menuOpen, collapsed }">
      <div class="sidebar-head">
        <strong class="sidebar-brand">MediaTracker</strong>
        <button
          type="button"
          class="sidebar-collapse"
          :aria-expanded="!collapsed"
          :aria-label="collapsed ? 'Déplier le menu' : 'Replier le menu'"
          :title="collapsed ? 'Déplier le menu' : 'Replier le menu'"
          @click="toggleCollapsed"
        >
          <SidebarIcon :name="collapsed ? 'expand' : 'collapse'" />
        </button>
        <button
          type="button"
          class="menu-toggle"
          :aria-expanded="menuOpen"
          aria-label="Ouvrir le menu"
          @click="menuOpen = !menuOpen"
        >
          ☰
        </button>
      </div>
      <nav aria-label="Navigation principale">
        <template v-for="item in navItems" :key="item.key">
          <p v-if="item.heading" class="hint sidebar-group" :title="item.label">{{ item.label }}</p>
          <router-link
            v-else-if="item.to"
            :to="item.to"
            class="sidebar-link"
            :aria-label="collapsed ? item.label : undefined"
            :title="collapsed ? item.label : undefined"
          >
            <SidebarIcon :name="item.icon" />
            <span class="sidebar-link-label">{{ item.label }}</span>
          </router-link>
          <a
            v-else-if="item.href"
            class="sidebar-link"
            :href="item.href"
            target="_blank"
            rel="noopener noreferrer"
            :aria-label="collapsed ? item.label : undefined"
            :title="collapsed ? item.label : undefined"
          >
            <SidebarIcon :name="item.icon" />
            <span class="sidebar-link-label">{{ item.label }}</span>
          </a>
          <button
            v-else
            type="button"
            class="sidebar-link"
            :aria-label="collapsed ? item.label : item.label"
            :title="collapsed ? item.label : undefined"
            @click="onNavAction(item)"
          >
            <SidebarIcon :name="item.icon" />
            <span class="sidebar-link-label">{{ item.label }}</span>
          </button>
        </template>
      </nav>
      <div class="sidebar-foot">
        <p v-if="user" class="hint sidebar-user">{{ user.username }}</p>
        <p v-if="versionLabel" class="hint sidebar-version" :title="versionTitle">{{ versionLabel }}</p>
        <p class="hint sidebar-version">
          <a
            href="https://github.com/nico2511/Mediatracker-hive"
            target="_blank"
            rel="noopener"
          >Dépôt Mediatracker-hive</a>
        </p>
      </div>
    </aside>
    <main class="content">
      <ConnectivityBar />
      <router-view />
    </main>
    <MobileBottomNav v-if="!hideMobileNav" @menu="menuOpen = !menuOpen" />
  </div>
</template>

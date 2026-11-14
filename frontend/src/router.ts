import { createRouter, createWebHistory } from "vue-router";

import { sessionGate } from "./session";
import AppLayout from "./layouts/AppLayout.vue";
import DownloadsView from "./views/DownloadsView.vue";
import LibraryImportHistoryView from "./views/LibraryImportHistoryView.vue";
import LibraryView from "./views/LibraryView.vue";
import FollowView from "./views/FollowView.vue";
import HealthView from "./views/HealthView.vue";
import LoginView from "./views/LoginView.vue";
import SetupView from "./views/SetupView.vue";
import MediaDetailView from "./views/MediaDetailView.vue";
import MediaListView from "./views/MediaListView.vue";
import SettingsLayout from "./views/settings/SettingsLayout.vue";
import SettingsIndexView from "./views/settings/SettingsIndexView.vue";
import SettingsSectionView from "./views/settings/SettingsSectionView.vue";
import JournalView from "./views/JournalView.vue";
import TmdbHomeView from "./views/TmdbHomeView.vue";
import TmdbCatalogView from "./views/TmdbCatalogView.vue";
import HiveShell from "./views/hive/HiveShell.vue";
import HiveCatalogueView from "./views/hive/HiveCatalogueView.vue";
import HiveWorkView from "./views/hive/HiveWorkView.vue";
import HiveJoinView from "./views/hive/HiveJoinView.vue";
import HiveRucheMembersView from "./views/hive/HiveRucheMembersView.vue";
import SettingsHiveSection from "./views/settings/SettingsHiveSection.vue";
import SettingsSourcesSection from "./views/settings/SettingsSourcesSection.vue";
import { useHiveEnabled } from "./composables/useHive";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/login", component: LoginView, meta: { public: true } },
    { path: "/configuration", component: SetupView, meta: { wizard: true } },
    {
      path: "/",
      component: AppLayout,
      children: [
        { path: "", component: TmdbHomeView },
        { path: "suivre", component: FollowView },
        { path: "medias", component: MediaListView },
        { path: "nouveautes/series", component: TmdbCatalogView, props: { mediaType: "series" } },
        { path: "nouveautes/films", component: TmdbCatalogView, props: { mediaType: "film" } },
        { path: "medias/:id", component: MediaDetailView },
        { path: "telechargements", component: DownloadsView },
        { path: "bibliotheque", component: LibraryView },
        { path: "bibliotheque/importer-historique", component: LibraryImportHistoryView },
        { path: "journal", component: JournalView },
        { path: "tmdb", redirect: "/" },
        { path: "tmdb/films", redirect: "/nouveautes/films" },
        { path: "tmdb/series", redirect: "/nouveautes/series" },
        {
          path: "hive",
          component: HiveShell,
          meta: { hive: true },
          children: [
            { path: "", redirect: "/hive/catalogue" },
            { path: "catalogue", component: HiveCatalogueView },
            { path: "catalogue/:workKey", component: HiveWorkView },
            { path: "rejoindre", component: HiveJoinView },
            { path: "ruches/:rucheIdHex/membres", component: HiveRucheMembersView },
          ],
        },
        {
          path: "reglages",
          component: SettingsLayout,
          children: [
            { path: "", name: "settings-index", component: SettingsIndexView },
            { path: "hive", name: "settings-hive", component: SettingsHiveSection },
            { path: "sources", name: "settings-sources", component: SettingsSourcesSection },
            { path: "comet", redirect: "/reglages/sources" },
            { path: ":section", name: "settings-section", component: SettingsSectionView },
          ],
        },
        { path: "performance", redirect: "/sante" },
        { path: "sante", component: HealthView },
      ],
    },
  ],
});

const { hiveEnabled, refreshHive } = useHiveEnabled();

router.beforeEach(async (to) => {
  if (to.meta.public) {
    return true;
  }
  const gate = await sessionGate();
  if (!gate.signedIn) {
    if (to.meta.wizard) {
      return true;
    }
    if (gate.needsPassword) {
      return "/configuration";
    }
    return "/login";
  }
  if (to.path === "/configuration") {
    return true;
  }
  if (!gate.setupCompleted) {
    return "/configuration";
  }
  await refreshHive();
  if (to.meta.hive && !hiveEnabled.value) {
    return "/reglages/hive";
  }
  return true;
});

export default router;

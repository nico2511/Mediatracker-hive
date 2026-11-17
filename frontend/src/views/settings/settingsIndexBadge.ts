import type { Ref } from "vue";

type StatusRefs = {
  serviceStatusLoaded: Ref<boolean>;
  adConnected: Ref<boolean>;
  jellyfinConfigured: Ref<boolean>;
  downloadReady: Ref<boolean>;
  hiveEnabled: Ref<boolean>;
};

/** Libellé de pastille sur l’index des réglages, ou null si aucune. */
export function settingsIndexBadgeLabel(slug: string, status: StatusRefs): string | null {
  if (!status.serviceStatusLoaded.value) {
    return null;
  }
  switch (slug) {
    case "alldebrid":
      return status.adConnected.value ? null : "à connecter";
    case "jellyfin":
      return status.jellyfinConfigured.value ? null : "à connecter";
    case "sources":
      return status.downloadReady.value ? null : "à connecter";
    case "hive":
      return status.hiveEnabled.value ? null : "désactivé";
    default:
      return null;
  }
}
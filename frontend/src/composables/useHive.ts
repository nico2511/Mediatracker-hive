import { onMounted, ref } from "vue";

import { api } from "../api";
import { fetchSettings } from "../settings";

const hiveEnabled = ref(false);
let loadGeneration = 0;

/** Met à jour l’état Hive partagé (navigation). */
export async function refreshHiveState(): Promise<void> {
  const gen = ++loadGeneration;
  try {
    const overview = await api<{ enabled: boolean }>("/api/v1/hive/overview");
    if (gen === loadGeneration) {
      hiveEnabled.value = overview.enabled;
    }
  } catch {
    try {
      const page = await fetchSettings();
      const row = page.settings?.find((item) => item.key === "hive_enabled");
      if (gen === loadGeneration) {
        hiveEnabled.value = row?.value === true || row?.value === "true";
      }
    } catch {
      if (gen === loadGeneration) {
        hiveEnabled.value = false;
      }
    }
  }
}

export function useHiveEnabled() {
  onMounted(() => {
    void refreshHiveState();
  });

  return { hiveEnabled, refreshHive: refreshHiveState };
}

/** Lecture de l’état partagé (tests / diagnostics). */
export function hiveEnabledRef() {
  return hiveEnabled;
}

import { api, type SettingRow } from "./api";
import { invalidateJellyfinWeb } from "./layoutCache";

export interface SettingsPage {
  settings: SettingRow[];
}

let cached: SettingsPage | null = null;
let inflight: Promise<SettingsPage> | null = null;

export function invalidateSettings(): void {
  cached = null;
  inflight = null;
}

export async function fetchSettings(): Promise<SettingsPage> {
  if (cached) {
    return cached;
  }
  if (!inflight) {
    inflight = api<SettingsPage>("/api/v1/settings")
      .then((page) => {
        cached = page;
        return page;
      })
      .finally(() => {
        inflight = null;
      });
  }
  return inflight;
}

export async function patchSettings(body: { values: Record<string, unknown> }): Promise<void> {
  await api("/api/v1/settings", { method: "PATCH", body: JSON.stringify(body) });
  invalidateSettings();
  if (Object.keys(body.values).some((key) => key.startsWith("jellyfin_"))) {
    invalidateJellyfinWeb();
  }
}

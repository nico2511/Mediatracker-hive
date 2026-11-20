import { api, type SettingRow } from "./api";
import { sanitizePatchValues } from "./settingsMeta";
import { invalidateJellyfinWeb } from "./layoutCache";

export interface SourceUrlDefaults {
  comet_base_urls: string[];
  torrentio_base_url: string;
}

export interface ClientConstants {
  hive_join_wait_hours: number;
  hive_media_sharing_summary_id_limit: number;
}

export interface SettingsPage {
  settings: SettingRow[];
  source_defaults?: SourceUrlDefaults;
  client_constants?: ClientConstants;
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
  const values = sanitizePatchValues(body.values);
  await api("/api/v1/settings", { method: "PATCH", body: JSON.stringify({ values }) });
  invalidateSettings();
  if (Object.keys(values).some((key) => key.startsWith("jellyfin_"))) {
    invalidateJellyfinWeb();
  }
}

import { type InjectionKey, type Ref, inject, onMounted, provide, ref, watch } from "vue";

import { api, userFacingApiError } from "../api";
import type { HiveCatalogueVersionRow } from "../components/hive/HiveCatalogueVersionList.vue";

export interface HiveCatalogueEpisodeWork {
  work_key: string;
  type: string | null;
  season: number | null;
  episode: number | null;
  versions: HiveCatalogueVersionRow[];
}

export interface HiveSeriesCatalogueContext {
  episodeWork: (season: number | null | undefined, episode: number | null | undefined) => HiveCatalogueEpisodeWork | null;
  loading: Ref<boolean>;
  error: Ref<string>;
  refresh: () => Promise<void>;
}

const key: InjectionKey<HiveSeriesCatalogueContext> = Symbol("hiveSeriesCatalogue");

function slotKey(season: number, episode: number): string {
  return `${season}:${episode}`;
}

export function provideHiveSeriesCatalogue(tmdbId: Ref<number | null | undefined>, enabled: Ref<boolean>) {
  const loading = ref(false);
  const error = ref("");
  const bySlot = ref<Map<string, HiveCatalogueEpisodeWork>>(new Map());

  async function refresh() {
    const id = tmdbId.value;
    if (!enabled.value || id == null) {
      bySlot.value = new Map();
      error.value = "";
      return;
    }
    loading.value = true;
    error.value = "";
    try {
      const params = new URLSearchParams({ tmdb_id: String(id), media_type: "series" });
      const page = await api<{ items: HiveCatalogueEpisodeWork[] }>(`/api/v1/hive/catalogue?${params.toString()}`);
      const items = page.items ?? [];
      const next = new Map<string, HiveCatalogueEpisodeWork>();

      for (const row of items) {
        if (row.season != null && row.episode != null && row.versions?.length) {
          next.set(slotKey(row.season, row.episode), row);
        }
      }

      const shell = items.find((row) => row.season == null && row.episode == null);
      if (shell?.work_key) {
        const detail = await api<HiveCatalogueEpisodeWork & { episodes?: HiveCatalogueEpisodeWork[] }>(
          `/api/v1/hive/catalogue/${encodeURIComponent(shell.work_key)}`,
        );
        for (const ep of detail.episodes ?? []) {
          if (ep.season != null && ep.episode != null && ep.versions?.length) {
            next.set(slotKey(ep.season, ep.episode), ep);
          }
        }
      }

      bySlot.value = next;
    } catch (err) {
      bySlot.value = new Map();
      error.value = userFacingApiError(err, "Catalogue Hive indisponible.");
    } finally {
      loading.value = false;
    }
  }

  const ctx: HiveSeriesCatalogueContext = {
    loading,
    error,
    refresh,
    episodeWork(season, episode) {
      if (season == null || episode == null) {
        return null;
      }
      return bySlot.value.get(slotKey(season, episode)) ?? null;
    },
  };

  provide(key, ctx);

  onMounted(() => void refresh());
  watch([tmdbId, enabled], () => void refresh());

  return ctx;
}

export function useHiveSeriesCatalogue(): HiveSeriesCatalogueContext | null {
  return inject(key, null);
}
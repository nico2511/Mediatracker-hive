import { requestAppSettingsCachesIfEmpty } from "../appSettingsCaches";
import { hiveMediaSummaryLimit } from "../clientConstants";
import type { HiveMediaSummary } from "../hive/hiveBadges";

type HiveMediaSummaryRow = HiveMediaSummary & { media_id: number };

export async function fetchHiveMediaSummaries(
  request: <T>(path: string) => Promise<T>,
  mediaIds: number[],
): Promise<Record<number, HiveMediaSummary>> {
  if (!mediaIds.length) {
    return {};
  }
  requestAppSettingsCachesIfEmpty();
  const limit = hiveMediaSummaryLimit();
  const limited = limit === null ? mediaIds : mediaIds.slice(0, limit);
  const rows = await request<HiveMediaSummaryRow[]>(
    `/api/v1/hive/media/sharing-summary?ids=${limited.join(",")}`,
  );
  const next: Record<number, HiveMediaSummary> = {};
  for (const row of rows) {
    next[row.media_id] = {
      shared: row.shared,
      transfer_active: row.transfer_active,
      preparing: row.preparing,
      pull_only: row.pull_only,
    };
  }
  return next;
}

/** Release list loading; Hive badge refresh is scheduled separately when the list is ready. */
export async function releaseLoadingThenRefreshHive(
  setLoading: (value: boolean) => void,
  refresh?: () => Promise<void>,
): Promise<void> {
  setLoading(false);
  if (refresh) {
    await refresh();
  }
}

import { describe, expect, it, vi } from "vitest";

import {
  fetchHiveMediaSummaries,
  HIVE_MEDIA_SUMMARY_LIMIT,
  releaseLoadingThenRefreshHive,
} from "./hiveMediaSummaries";

describe("fetchHiveMediaSummaries", () => {
  it("calls the batch endpoint with a comma-separated id list", async () => {
    const request = vi.fn().mockResolvedValue([
      { media_id: 11, shared: true, transfer_active: false, preparing: false, pull_only: false },
      { media_id: 22, shared: false, transfer_active: true, preparing: false, pull_only: true },
    ]);
    const out = await fetchHiveMediaSummaries(request, [11, 22]);
    expect(request).toHaveBeenCalledWith("/api/v1/hive/media/sharing-summary?ids=11,22");
    expect(out[11]?.shared).toBe(true);
    expect(out[22]?.transfer_active).toBe(true);
  });

  it("caps the number of requested ids", async () => {
    const request = vi.fn().mockResolvedValue([]);
    const ids = Array.from({ length: HIVE_MEDIA_SUMMARY_LIMIT + 5 }, (_, index) => index + 1);
    await fetchHiveMediaSummaries(request, ids);
    const path = request.mock.calls[0]?.[0] as string;
    const requested = path.split("ids=")[1]?.split(",") ?? [];
    expect(requested).toHaveLength(HIVE_MEDIA_SUMMARY_LIMIT);
  });
});

describe("releaseLoadingThenRefreshHive", () => {
  it("sets loading to false before awaiting Hive summaries", async () => {
    const order: string[] = [];
    const loading = { value: true };
    await releaseLoadingThenRefreshHive(
      (value) => {
        loading.value = value;
        order.push(value ? "loading-on" : "loading-off");
      },
      async () => {
        order.push("hive-start");
        expect(loading.value).toBe(false);
        order.push("hive-end");
      },
    );
    expect(order).toEqual(["loading-off", "hive-start", "hive-end"]);
  });
});
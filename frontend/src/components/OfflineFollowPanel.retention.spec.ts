import { mount, flushPromises } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setFixedRetentionForTests } from "../fixedRetention";
import OfflineFollowPanel from "./OfflineFollowPanel.vue";

vi.mock("../pwa/connectivity", () => ({
  useConnectivity: () => ({
    mode: { value: "offline" },
    queueCount: { value: 2 },
    refreshQueueCount: vi.fn(),
    syncResults: { value: [] },
    clearSyncResults: vi.fn(),
    tryFlushQueue: vi.fn(),
    retryConnection: vi.fn(),
    lastProbeAt: { value: null },
  }),
}));

vi.mock("../pwa/offlineQueue", () => ({
  listQueueItems: vi.fn(async () => [
    { id: "a", tmdb_id: 1, type: "series", added_at: "2026-01-01T12:00:00.000Z" },
    { id: "b", tmdb_id: 2, type: "film", added_at: "2026-01-02T12:00:00.000Z" },
  ]),
  removeQueueItem: vi.fn(),
  updateQueueItemType: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: { file: "1" } }),
  useRouter: () => ({ push: vi.fn() }),
}));

describe("OfflineFollowPanel retention label", () => {
  beforeEach(() => {
    setFixedRetentionForTests({
      event_journal_days: 30,
      source_state_days: 21,
      metrics_history_days: 7,
      incomplete_orphan_keep_days: 3,
    });
  });

  it("mentionne la durée serveur dans le sous-titre de file", async () => {
    const wrapper = mount(OfflineFollowPanel);
    await flushPromises();
    expect(wrapper.text()).toContain("gardés 21 jours");
  });
});
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ref } from "vue";

import { ApiError } from "../api";
import type { HiveShareUiState } from "../hive/hiveBadges";
import {
  HIVE_SHARE_POLL_INTERVAL_MS,
  HIVE_SHARE_POLL_MAX_ATTEMPTS,
  HIVE_SHARE_POLL_MAX_MS,
  hiveShareFicheStatus,
} from "../hive/hiveShareStatus";
import { HIVE_SHARE_POLL_CONTRACT, useHiveSharePolling } from "./useHiveSharePolling";

function waitingState(tick: number): HiveShareUiState {
  return {
    shareable: false,
    shared: false,
    exclude_all: false,
    excluded_ruche_ids: [`tick-${tick}`],
    preparing: true,
    transfer_active: false,
    transfer_progress_pct: null,
    hive_keep: false,
    keep: false,
    pull_only: false,
    has_file: true,
    catalog_entry_local: false,
  };
}

function terminalShareState(): HiveShareUiState {
  return {
    shareable: true,
    shared: true,
    exclude_all: false,
    excluded_ruche_ids: [],
    preparing: false,
    transfer_active: false,
    transfer_progress_pct: null,
    hive_keep: false,
    keep: false,
    pull_only: false,
    has_file: true,
    catalog_entry_local: true,
  };
}

describe("useHiveSharePolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it(`contrat ${HIVE_SHARE_POLL_CONTRACT}`, async () => {
    let tick = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      return waitingState((tick += 1));
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(1),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => true,
      fetchSharing: fetch,
    });
    poll.startPolling(true);
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    for (let i = 0; i < HIVE_SHARE_POLL_MAX_ATTEMPTS; i += 1) {
      await vi.advanceTimersByTimeAsync(HIVE_SHARE_POLL_INTERVAL_MS);
      await Promise.resolve();
    }
    await vi.advanceTimersByTimeAsync(HIVE_SHARE_POLL_INTERVAL_MS);
    await Promise.resolve();
    expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(1 + HIVE_SHARE_POLL_MAX_ATTEMPTS - 1);
    expect(poll._debug().pollCount).toBe(HIVE_SHARE_POLL_MAX_ATTEMPTS);
    expect(poll.pollExhausted.value).toBe(true);
    poll.stopPolling();
  });

  it("retour visible respecte cooldown et compte les polls", async () => {
    let visible = true;
    let calls = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      calls += 1;
      if (calls === 1) {
        throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 12);
      }
      return waitingState(1);
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(8),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => visible,
      fetchSharing: fetch,
    });
    poll.startPolling(true);
    await Promise.resolve();
    visible = false;
    poll.onFicheHidden();
    visible = true;
    poll.resumeIfPaused();
    await vi.advanceTimersByTimeAsync(3_000);
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(9_000);
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(poll._debug().pollCount).toBeGreaterThanOrEqual(1);
    poll.stopPolling();
  });

  it("mutation et reload attendent Retry-After actif", async () => {
    let calls = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      calls += 1;
      if (calls === 1) {
        throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 20);
      }
      return waitingState(calls);
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(9),
      hiveEnabled: ref(true),
      state: ref(null),
      visible: () => true,
      fetchSharing: fetch,
    });
    await poll.reloadFromNavigation();
    await Promise.resolve();
    expect(calls).toBe(1);
    await poll.afterMutationFetch();
    await vi.advanceTimersByTimeAsync(5_000);
    await Promise.resolve();
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(15_000);
    await Promise.resolve();
    expect(calls).toBeGreaterThanOrEqual(2);
    poll.stopPolling();
  });

  it("reload 503 initial affiche erreur et reprise bornée", async () => {
    let calls = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      calls += 1;
      throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 5);
    });
    const state = ref<HiveShareUiState | null>(null);
    const poll = useHiveSharePolling({
      mediaItemId: ref(11),
      hiveEnabled: ref(true),
      state,
      visible: () => true,
      fetchSharing: fetch,
    });
    await poll.reloadFromNavigation();
    await Promise.resolve();
    expect(poll.shareRequestError.value).toContain("Base occupée");
    const ui = hiveShareFicheStatus(state.value, { requestError: poll.shareRequestError.value });
    expect(ui.phase).toBe("error");
    await vi.advanceTimersByTimeAsync(5_000);
    await Promise.resolve();
    expect(calls).toBe(2);
    poll.stopPolling();
  });

  it("Actualiser après exhaustion respecte Retry-After", async () => {
    let calls = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      calls += 1;
      if (calls === 1) {
        throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 12);
      }
      return waitingState(1);
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(10),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => true,
      fetchSharing: fetch,
    });
    vi.setSystemTime(0);
    poll.startPolling(true);
    await Promise.resolve();
    const earliest = poll._debug().earliestRetryAt;
    poll.pollExhausted.value = true;
    poll.manualRefresh();
    await vi.advanceTimersByTimeAsync(3_000);
    await Promise.resolve();
    expect(calls).toBe(1);
    expect(poll._debug().earliestRetryAt).toBe(earliest);
    await vi.advanceTimersByTimeAsync(9_000);
    await Promise.resolve();
    expect(calls).toBeGreaterThanOrEqual(2);
    poll.stopPolling();
  });

  it("reload onglet caché : aucun GET, reprise via orchestrateur à visible", async () => {
    let visible = false;
    const fetch = vi.fn(async () => waitingState(1));
    const poll = useHiveSharePolling({
      mediaItemId: ref(20),
      hiveEnabled: ref(true),
      state: ref(null),
      visible: () => visible,
      fetchSharing: fetch,
    });
    const pending = poll.reloadFromNavigation();
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(0);
    visible = true;
    poll.resumeIfPaused();
    await pending;
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    poll.stopPolling();
  });

  it("reload/mutation hidden avec état terminal connu : 1 GET au retour visible", async () => {
    let visible = false;
    const fetch = vi.fn(async () => waitingState(41));
    const state = ref<HiveShareUiState>(terminalShareState());
    const poll = useHiveSharePolling({
      mediaItemId: ref(41),
      hiveEnabled: ref(true),
      state,
      visible: () => visible,
      fetchSharing: fetch,
    });
    poll.startPolling(false);
    poll.stopPolling();
    const reloadPending = poll.reloadFromNavigation();
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(0);
    poll.afterMutationFetch();
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(0);
    visible = true;
    poll.resumeIfPaused();
    await reloadPending;
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledTimes(1);
    poll.stopPolling();
  });

  it("activation Hive ne réinitialise pas le cooldown sur le même item", async () => {
    let calls = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      calls += 1;
      if (calls === 1) {
        throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 600);
      }
      return waitingState(1);
    });
    const hiveOn = ref(true);
    const poll = useHiveSharePolling({
      mediaItemId: ref(21),
      hiveEnabled: hiveOn,
      state: ref(null),
      visible: () => true,
      fetchSharing: fetch,
    });
    vi.setSystemTime(0);
    await poll.reloadFromNavigation();
    await Promise.resolve();
    const earliest = poll._debug().earliestRetryAt;
    expect(earliest).toBe(600_000);
    hiveOn.value = false;
    poll.stopPolling();
    hiveOn.value = true;
    await poll.reloadFromNavigation();
    await vi.advanceTimersByTimeAsync(100_000);
    await Promise.resolve();
    expect(calls).toBe(1);
    expect(poll._debug().earliestRetryAt).toBe(earliest);
    poll.stopPolling();
  });

  it("Retry-After 600 s conservé sans plafond 300 s", async () => {
    const fetch = vi.fn(async () => {
      throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 600);
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(22),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => true,
      fetchSharing: fetch,
    });
    vi.setSystemTime(0);
    poll.startPolling(true);
    await Promise.resolve();
    expect(poll.shareRetryAfterAt.value).toBe(600_000);
    expect(poll._debug().earliestRetryAt).toBe(600_000);
    poll.stopPolling();
  });

  it("aucun GET avant fin du cooldown (Actualiser / exhaustion)", async () => {
    let calls = 0;
    const fetch = vi.fn(async (_itemId, signal: AbortSignal) => {
      if (signal.aborted) {
        throw new DOMException("Aborted", "AbortError");
      }
      calls += 1;
      if (calls === 1) {
        throw new ApiError(503, "Base occupée", undefined, undefined, undefined, 12);
      }
      return waitingState(1);
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(24),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => true,
      fetchSharing: fetch,
    });
    poll.startPolling(true);
    await Promise.resolve();
    poll.pollExhausted.value = true;
    poll.manualRefresh();
    await vi.advanceTimersByTimeAsync(11_000);
    await Promise.resolve();
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(1_000);
    await Promise.resolve();
    expect(calls).toBe(2);
    poll.stopPolling();
  });

  it("finally de A n’efface pas timer 120 s ni busy de B", async () => {
    vi.setSystemTime(0);
    let settleA: (() => void) | null = null;
    let calls = 0;
    const state = ref<HiveShareUiState | null>(waitingState(0));
    const fetch = vi.fn((_itemId, signal: AbortSignal) => {
      calls += 1;
      if (calls === 1) {
        return new Promise<HiveShareUiState>((resolve, reject) => {
          signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
          settleA = () => resolve(waitingState(88));
        });
      }
      return new Promise<HiveShareUiState>((resolve, reject) => {
        signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
        setTimeout(() => resolve(waitingState(99)), 200_000);
      });
    });
    const poll = useHiveSharePolling({
      mediaItemId: ref(30),
      hiveEnabled: ref(true),
      state,
      visible: () => true,
      fetchSharing: fetch,
    });
    poll.startPolling(true);
    await Promise.resolve();
    expect(calls).toBe(1);
    poll.manualRefresh();
    await Promise.resolve();
    expect(calls).toBe(2);
    expect(poll.pollBusy.value).toBe(true);
    settleA?.();
    await Promise.resolve();
    expect(poll.pollBusy.value).toBe(true);
    await vi.advanceTimersByTimeAsync(HIVE_SHARE_POLL_MAX_MS + 1);
    await Promise.resolve();
    expect(poll.pollExhausted.value).toBe(true);
    expect(state.value?.excluded_ruche_ids).not.toEqual(["tick-88"]);
    poll.stopPolling();
  });

  it("annule un fetch lent à l’échéance 120 s", async () => {
    vi.setSystemTime(0);
    let resolveLate: (() => void) | null = null;
    const fetch = vi.fn(
      (_itemId, signal: AbortSignal) =>
        new Promise<HiveShareUiState>((resolve, reject) => {
          if (signal.aborted) {
            reject(new DOMException("Aborted", "AbortError"));
            return;
          }
          signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
          resolveLate = () => resolve(waitingState(99));
        }),
    );
    const poll = useHiveSharePolling({
      mediaItemId: ref(23),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => true,
      fetchSharing: fetch,
    });
    poll.startPolling(true);
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(HIVE_SHARE_POLL_MAX_MS + 1);
    await Promise.resolve();
    expect(poll.pollExhausted.value).toBe(true);
    resolveLate?.();
    await Promise.resolve();
    poll.stopPolling();
  });

  it("borne 120s revérifiée au déclenchement du timer", async () => {
    vi.setSystemTime(0);
    const fetch = vi.fn(async () => waitingState(1));
    const poll = useHiveSharePolling({
      mediaItemId: ref(2),
      hiveEnabled: ref(true),
      state: ref(waitingState(0)),
      visible: () => true,
      fetchSharing: fetch,
    });
    poll.startPolling(true);
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    const before = fetch.mock.calls.length;
    vi.setSystemTime(HIVE_SHARE_POLL_MAX_MS + 1);
    await vi.advanceTimersByTimeAsync(HIVE_SHARE_POLL_INTERVAL_MS);
    await Promise.resolve();
    expect(fetch.mock.calls.length).toBe(before);
    expect(poll.pollExhausted.value).toBe(true);
    poll.stopPolling();
  });
});

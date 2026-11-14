import { onUnmounted, ref, type Ref } from "vue";

import { ApiError, api, isTransientHttpStatus, parseRetryAfterSeconds } from "../api";
import type { HiveShareUiState } from "../hive/hiveBadges";
import {
  HIVE_SHARE_POLL_INTERVAL_MS,
  HIVE_SHARE_POLL_MAX_ATTEMPTS,
  HIVE_SHARE_POLL_MAX_MS,
  shouldPollHiveSharing,
} from "../hive/hiveShareStatus";

/** 1er GET initial + au plus 24 polls planifiés (25 requêtes max si toujours en préparation). */
export const HIVE_SHARE_POLL_CONTRACT =
  "1 requête initiale + 24 polls maximum (comptées séparément)" as const;

type FetchFn = (itemId: number, signal: AbortSignal) => Promise<HiveShareUiState>;
type TickKind = "initial" | "poll" | "manual" | "reload" | "mutation";

function isAbortError(err: unknown): boolean {
  return (
    (err instanceof DOMException && err.name === "AbortError") ||
    (typeof err === "object" &&
      err !== null &&
      "name" in err &&
      (err as { name: string }).name === "AbortError")
  );
}

function retryDelayMs(seconds: number): number {
  return Math.max(0, seconds * 1000);
}

export function useHiveSharePolling(options: {
  mediaItemId: Ref<number | null>;
  hiveEnabled: Ref<boolean>;
  state: Ref<HiveShareUiState | null>;
  visible: () => boolean;
  fetchSharing?: FetchFn;
}) {
  const pollExhausted = ref(false);
  const pollBusy = ref(false);
  const shareRequestError = ref<string | null>(null);
  /** Horodatage ms : prochain essai autorisé (Retry-After serveur, conservé après borne 120 s). */
  const shareRetryAfterAt = ref(0);

  const fetchSharing: FetchFn =
    options.fetchSharing ??
    ((itemId, signal) => api<HiveShareUiState>(`/api/v1/hive/items/${itemId}/sharing`, { signal }));

  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlightController: AbortController | null = null;
  let generation = 0;
  let pollCount = 0;
  let sessionStartedAt = 0;
  let sessionActive = false;
  let pausedForHidden = false;
  let pausedKind: TickKind = "poll";
  let earliestRetryAt = 0;
  let boundItemId: number | null = null;
  let sessionDeadlineTimer: ReturnType<typeof setTimeout> | null = null;
  let sessionDeadlineOwner = 0;
  let inFlightRequestId = 0;
  let nextFetchRequestId = 0;

  function clearSessionDeadlineTimer() {
    if (sessionDeadlineTimer !== null) {
      clearTimeout(sessionDeadlineTimer);
      sessionDeadlineTimer = null;
    }
    sessionDeadlineOwner = 0;
  }

  function releaseSessionDeadlineTimer(requestId: number) {
    if (sessionDeadlineOwner !== requestId) {
      return;
    }
    clearSessionDeadlineTimer();
  }

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function abortInFlight() {
    if (inFlightController) {
      inFlightController.abort();
      inFlightController = null;
    }
  }

  function invalidateRequests() {
    clearTimer();
    clearSessionDeadlineTimer();
    abortInFlight();
    generation += 1;
    sessionActive = false;
    pausedForHidden = false;
    pollBusy.value = false;
  }

  function stopPolling() {
    invalidateRequests();
  }

  function sessionTimedOut(): boolean {
    return Date.now() - sessionStartedAt > HIVE_SHARE_POLL_MAX_MS;
  }

  function pollsExhausted(): boolean {
    return pollCount >= HIVE_SHARE_POLL_MAX_ATTEMPTS;
  }

  function markExhausted() {
    pollExhausted.value = true;
    sessionActive = false;
    clearTimer();
    clearSessionDeadlineTimer();
    abortInFlight();
  }

  function resetSessionCounters() {
    pollCount = 0;
    sessionStartedAt = Date.now();
    pollExhausted.value = false;
  }

  function clearRetryCooldownForNewItem() {
    earliestRetryAt = 0;
    shareRetryAfterAt.value = 0;
  }

  function delayRespectingRetry(requestedMs: number): number {
    const now = Date.now();
    const fromRetry = Math.max(0, earliestRetryAt - now);
    return Math.max(requestedMs, fromRetry);
  }

  function noteRetryAfter(seconds: number) {
    earliestRetryAt = Math.max(earliestRetryAt, Date.now() + retryDelayMs(seconds));
    shareRetryAfterAt.value = earliestRetryAt;
  }

  function applyFreshState(next: HiveShareUiState, gen: number, itemId: number) {
    if (gen !== generation || options.mediaItemId.value !== itemId) {
      return;
    }
    options.state.value = { ...next };
    shareRequestError.value = null;
    shareRetryAfterAt.value = 0;
    if (!shouldPollHiveSharing(options.state.value, { pollExhausted: pollExhausted.value })) {
      stopPolling();
    }
  }

  function limitsReached(): boolean {
    return pollExhausted.value || pollsExhausted() || sessionTimedOut();
  }

  function armTimer(gen: number, itemId: number, delayMs: number, onFire: () => void) {
    clearTimer();
    if (gen !== generation || options.mediaItemId.value !== itemId) {
      return;
    }
    const wait = delayRespectingRetry(delayMs);
    timer = setTimeout(() => {
      timer = null;
      if (gen !== generation || options.mediaItemId.value !== itemId) {
        return;
      }
      if (!options.visible()) {
        pausedForHidden = true;
        pausedKind = "poll";
        return;
      }
      if (limitsReached()) {
        markExhausted();
        return;
      }
      onFire();
    }, wait);
  }

  async function executeFetch(itemId: number, gen: number, kind: TickKind): Promise<void> {
    const requestId = ++nextFetchRequestId;
    abortInFlight();
    clearSessionDeadlineTimer();
    const deadlineLeft = sessionStartedAt + HIVE_SHARE_POLL_MAX_MS - Date.now();
    if (deadlineLeft <= 0) {
      markExhausted();
      return;
    }
    const controller = new AbortController();
    inFlightController = controller;
    inFlightRequestId = requestId;
    sessionDeadlineOwner = requestId;
    sessionDeadlineTimer = setTimeout(() => {
      if (sessionDeadlineOwner !== requestId) {
        return;
      }
      sessionDeadlineTimer = null;
      sessionDeadlineOwner = 0;
      if (inFlightController === controller && inFlightRequestId === requestId) {
        controller.abort();
      }
      if (gen === generation) {
        markExhausted();
      }
    }, deadlineLeft);
    pollBusy.value = true;
    try {
      const next = await fetchSharing(itemId, controller.signal);
      if (gen !== generation || options.mediaItemId.value !== itemId || controller.signal.aborted) {
        return;
      }
      applyFreshState(next, gen, itemId);
      if (
        gen === generation &&
        options.mediaItemId.value === itemId &&
        sessionActive &&
        shouldPollHiveSharing(options.state.value, { pollExhausted: pollExhausted.value })
      ) {
        orchestrate("poll", { defer: true });
      }
    } catch (err) {
      if (isAbortError(err) || controller.signal.aborted) {
        return;
      }
      if (gen !== generation || options.mediaItemId.value !== itemId) {
        return;
      }
      const apiMsg =
        err instanceof ApiError ? err.message : "Impossible de lire l’état de partage.";
      if (err instanceof ApiError && (err.status === 503 || err.status === 429)) {
        shareRequestError.value = apiMsg;
        const sec = err.retryAfterSeconds ?? parseRetryAfterSeconds(null);
        noteRetryAfter(sec);
        sessionActive = true;
        orchestrate("poll", { defer: true, minDelayMs: retryDelayMs(sec) });
        return;
      }
      if (err instanceof ApiError && isTransientHttpStatus(err.status)) {
        shareRequestError.value = apiMsg;
        sessionActive = true;
        orchestrate("poll", { defer: true });
        return;
      }
      shareRequestError.value = apiMsg;
    } finally {
      releaseSessionDeadlineTimer(requestId);
      if (inFlightController === controller && inFlightRequestId === requestId) {
        inFlightController = null;
      }
      if (gen === generation && inFlightRequestId === requestId) {
        pollBusy.value = false;
      }
    }
  }

  function orchestrate(
    kind: TickKind,
    opts: { defer?: boolean; minDelayMs?: number; countAsPoll?: boolean } = {},
  ) {
    const itemId = options.mediaItemId.value;
    if (!options.hiveEnabled.value || itemId == null) {
      return;
    }
    const gen = generation;
    const countAsPoll = opts.countAsPoll ?? kind === "poll";

    if (opts.defer) {
      const delay = opts.minDelayMs ?? HIVE_SHARE_POLL_INTERVAL_MS;
      if (!sessionActive) {
        sessionActive = true;
      }
      armTimer(gen, itemId, delay, () => orchestrate("poll", { countAsPoll: true }));
      return;
    }

    if (!options.visible()) {
      pausedForHidden = true;
      pausedKind = kind;
      return;
    }
    pausedForHidden = false;

    const now = Date.now();
    if (now < earliestRetryAt) {
      sessionActive = true;
      armTimer(gen, itemId, earliestRetryAt - now, () => orchestrate(kind, opts));
      return;
    }

    if (countAsPoll && limitsReached()) {
      markExhausted();
      return;
    }

    clearTimer();
    abortInFlight();
    sessionActive = true;

    if (countAsPoll) {
      pollCount += 1;
    }

    void executeFetch(itemId, gen, kind);
  }

  function beginNewSession(immediate: boolean) {
    invalidateRequests();
    resetSessionCounters();
    sessionActive = true;
    orchestrate(immediate ? "initial" : "poll", { countAsPoll: !immediate });
  }

  function startPolling(immediate = true) {
    beginNewSession(immediate);
  }

  function onExternalStateLoaded() {
    if (!sessionActive && shouldPollHiveSharing(options.state.value, { pollExhausted: pollExhausted.value })) {
      resetSessionCounters();
      sessionActive = true;
      orchestrate("initial", { countAsPoll: false });
    }
  }

  async function reloadFromNavigation(): Promise<void> {
    const itemId = options.mediaItemId.value;
    if (itemId != null && itemId !== boundItemId) {
      clearRetryCooldownForNewItem();
      boundItemId = itemId;
    }
    invalidateRequests();
    resetSessionCounters();
    shareRequestError.value = null;
    if (!options.hiveEnabled.value || itemId == null) {
      options.state.value = null;
      boundItemId = null;
      return;
    }
    sessionActive = true;
    orchestrate("reload", { countAsPoll: false });
  }

  async function afterMutationFetch() {
    const itemId = options.mediaItemId.value;
    if (itemId == null || !options.hiveEnabled.value) {
      return;
    }
    invalidateRequests();
    resetSessionCounters();
    sessionActive = true;
    orchestrate("mutation", { countAsPoll: false });
  }

  function manualRefresh() {
    const itemId = options.mediaItemId.value;
    if (itemId == null || !options.hiveEnabled.value) {
      return;
    }
    if (pollExhausted.value) {
      pollExhausted.value = false;
      resetSessionCounters();
    }
    orchestrate("manual", { countAsPoll: false });
  }

  function resumeIfPaused() {
    if (!pausedForHidden || !sessionActive || !options.visible()) {
      return;
    }
    if (options.mediaItemId.value == null) {
      return;
    }
    const kind = pausedKind;
    const forcedRefresh = kind === "reload" || kind === "mutation" || kind === "manual" || kind === "initial";
    if (
      !forcedRefresh &&
      options.state.value &&
      !shouldPollHiveSharing(options.state.value, { pollExhausted: pollExhausted.value }) &&
      !shareRequestError.value
    ) {
      pausedForHidden = false;
      return;
    }
    pausedForHidden = false;
    orchestrate(kind, { countAsPoll: kind === "poll" });
  }

  function onItemIdChanged() {
    invalidateRequests();
    pollExhausted.value = false;
    shareRequestError.value = null;
    clearRetryCooldownForNewItem();
    boundItemId = null;
  }

  function onFicheHidden() {
    clearTimer();
    abortInFlight();
    if (sessionActive) {
      pausedForHidden = true;
    }
    pollBusy.value = false;
  }

  onUnmounted(stopPolling);

  return {
    pollExhausted,
    pollBusy,
    shareRequestError,
    shareRetryAfterAt,
    startPolling,
    onExternalStateLoaded,
    reloadFromNavigation,
    afterMutationFetch,
    manualRefresh,
    resumeIfPaused,
    onItemIdChanged,
    onFicheHidden,
    stopPolling,
    _debug: () => ({
      pollCount,
      sessionStartedAt,
      sessionActive,
      earliestRetryAt,
      generation,
    }),
  };
}

import { computed, onMounted, onUnmounted, ref } from "vue";

import {ApiError, api, userFacingApiError} from "../api";
import { LIBRARY_VERIFY_POLL_INTERVAL_MS } from "../constants/uiPollIntervals";
import { formatParisClock, parisDayKey, parseInstant } from "../time";

export interface VerifyAllProgress {
  running: boolean;
  started_at: string | null;
  processed: number;
  total: number;
  batch: number;
  stop_reason: string | null;
}

export interface VerifyAllProblem {
  media_id: number;
  title: string;
  summary: string;
  files_missing: number;
}

export interface VerifyAllSummary {
  at: string;
  processed: number;
  total: number;
  files_found: number;
  files_missing: number;
  links_available: number;
  step_errors: number;
  problems: VerifyAllProblem[];
  stopped_early: boolean;
  stop_reason: string | null;
  detail: string;
}

export interface VerifyAllState {
  progress: VerifyAllProgress;
  last_summary: VerifyAllSummary | null;
}

function formatDurationMs(ms: number): string {
  const totalSec = Math.max(0, Math.round(ms / 1000));
  if (totalSec < 60) {
    return `${totalSec} s`;
  }
  const minutes = Math.floor(totalSec / 60);
  const seconds = totalSec % 60;
  return seconds ? `${minutes} min ${seconds} s` : `${minutes} min`;
}

function todayParisKey(): string {
  return parisDayKey(new Date().toISOString());
}

function summaryWhenLabel(at: string): string {
  const day = parisDayKey(at);
  const today = todayParisKey();
  const clock = formatParisClock(at);
  if (day === today) {
    return clock ? `aujourd'hui à ${clock}` : "aujourd'hui";
  }
  const parsed = parseInstant(at);
  if (!parsed) {
    return "";
  }
  return parsed.toLocaleString("fr-FR", { timeZone: "Europe/Paris", dateStyle: "short", timeStyle: "short" });
}

export function useLibraryVerifyAll(onFinished?: () => void) {
  const state = ref<VerifyAllState | null>(null);
  const starting = ref(false);
  const fetchError = ref("");

  let stopped = false;
  let timer: number | null = null;
  let wasRunning = false;
  const jobStartedAt = ref<string | null>(null);

  const running = computed(() => Boolean(state.value?.progress.running));
  const progress = computed(() => state.value?.progress ?? null);
  const lastSummary = computed(() => state.value?.last_summary ?? null);

  const progressPercent = computed(() => {
    const row = progress.value;
    if (!row || !row.total) {
      return 0;
    }
    return Math.min(100, Math.round((row.processed / row.total) * 100));
  });

  const progressLine = computed(() => {
    const row = progress.value;
    if (!row?.running) {
      return "";
    }
    const batch = row.batch > 0 ? `lot ${row.batch}` : "préparation";
    return `${row.processed} / ${row.total} fiches · ${batch} · ${progressPercent.value} %`;
  });

  const resultHeadline = computed(() => {
    const summary = lastSummary.value;
    if (!summary || running.value) {
      return "";
    }
    const when = summaryWhenLabel(summary.at);
    const started = jobStartedAt.value || progress.value?.started_at;
    const duration = started ? formatDurationMs(new Date(summary.at).getTime() - new Date(started).getTime()) : "";
    const scope = `${summary.files_found.toLocaleString("fr-FR")} fichier(s) · ${summary.processed.toLocaleString("fr-FR")} fiche(s)`;
    const timing = duration ? `en ${duration}` : "";
    return `Tout vérifié ${when}${timing ? ` ${timing}` : ""} · ${scope}.`;
  });

  const resultActionsLine = computed(() => {
    const summary = lastSummary.value;
    if (!summary || running.value) {
      return "";
    }
    const points = summary.problems.length;
    const fixes = Math.max(0, summary.links_available);
    if (!points && !fixes && !summary.step_errors) {
      return "Rien à signaler.";
    }
    const bits: string[] = [];
    if (fixes) {
      bits.push(`${fixes} lien(s) disponible(s)`);
    }
    if (summary.step_errors) {
      bits.push(`${summary.step_errors} étape(s) en erreur`);
    }
    if (points) {
      bits.push(`${points} fiche(s) à voir`);
    }
    return bits.join(" · ");
  });

  const buttonLabel = computed(() => {
    if (running.value) {
      return "Vérification en cours…";
    }
    if (starting.value) {
      return "Démarrage…";
    }
    return "Tout vérifier";
  });

  const buttonDisabled = computed(() => running.value || starting.value);

  function clearTimer() {
    if (timer !== null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function schedulePoll() {
    clearTimer();
    if (stopped || !running.value) {
      return;
    }
    timer = window.setTimeout(async () => {
      if (document.visibilityState !== "visible") {
        schedulePoll();
        return;
      }
      await refresh();
      schedulePoll();
    }, LIBRARY_VERIFY_POLL_INTERVAL_MS);
  }

  async function refresh(): Promise<void> {
    try {
      const body = await api<VerifyAllState>("/api/v1/library/verify-all");
      const nowRunning = Boolean(body.progress.running);
      if (nowRunning && body.progress.started_at) {
        jobStartedAt.value = body.progress.started_at;
      }
      if (wasRunning && !nowRunning) {
        onFinished?.();
      }
      wasRunning = nowRunning;
      state.value = body;
      fetchError.value = "";
    } catch (err) {
      fetchError.value = userFacingApiError(err, "État de vérification indisponible.");
    }
  }

  async function start(): Promise<void> {
    if (buttonDisabled.value) {
      return;
    }
    starting.value = true;
    fetchError.value = "";
    try {
      const body = await api<VerifyAllState>("/api/v1/library/verify-all", { method: "POST" });
      wasRunning = Boolean(body.progress.running);
      state.value = body;
      schedulePoll();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        await refresh();
        schedulePoll();
      } else {
        fetchError.value = userFacingApiError(err, "Lancement impossible.");
      }
    } finally {
      starting.value = false;
    }
  }

  function onVisible() {
    if (document.visibilityState === "visible" && !stopped) {
      void refresh().then(() => {
        if (running.value) {
          schedulePoll();
        }
      });
    }
  }

  onMounted(() => {
    void refresh().then(() => {
      if (running.value) {
        schedulePoll();
      }
    });
    document.addEventListener("visibilitychange", onVisible);
  });

  onUnmounted(() => {
    stopped = true;
    clearTimer();
    document.removeEventListener("visibilitychange", onVisible);
  });

  return {
    state,
    fetchError,
    running,
    progress,
    lastSummary,
    progressPercent,
    progressLine,
    resultHeadline,
    resultActionsLine,
    buttonLabel,
    buttonDisabled,
    starting,
    refresh,
    start,
  };
}

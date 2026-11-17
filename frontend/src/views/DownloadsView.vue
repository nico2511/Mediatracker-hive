<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import StatusIcon from "../components/StatusIcon.vue";
import {api, userFacingApiError} from "../api";
import { useProgressStream, type ProgressPayload } from "../composables/useProgressStream";
import { formatBytes } from "../diskFormat";
import { DOWNLOAD_ACTIVE_STATES, mergeDownloadJobs, shouldResyncDownloadsFromSnapshot } from "../downloadsProgressMerge";
import { downloadQueueHint } from "../downloadsQueueHint";

interface Job {
  id: number;
  media_id: number;
  media_title: string;
  season: number | null;
  episode: number | null;
  hoster: string | null;
  source: string | null;
  state: string;
  step: string | null;
  progress: number;
  done_bytes: number;
  size: number | null;
  speed: number | null;
  eta_seconds: number | null;
  error_code: string | null;
  error: string | null;
  check: string | null;
  filename: string | null;
  release: string | null;
  attempts: number;
  next_retry_at?: string | null;
}

interface Page {
  items?: Job[];
  downloads?: Job[];
  queue_paused: boolean;
  queue_held?: boolean;
  queue_message: string | null;
}

const ACTIVE = DOWNLOAD_ACTIVE_STATES;
const RUNNING = new Set(["resolving", "downloading", "post_processing"]);
const QUEUED = new Set(["pending", "queued"]);

const router = useRouter();
const jobs = ref<Job[]>([]);
const paused = ref(false);
const held = ref(false);
const pauseMessage = ref<string | null>(null);
const error = ref("");
const showDone = ref(false);

const activeJobs = computed(() => jobs.value.filter((job) => ACTIVE.has(job.state)));
const runningJobs = computed(() => jobs.value.filter((job) => RUNNING.has(job.state)));
const queuedJobs = computed(() => jobs.value.filter((job) => QUEUED.has(job.state)));
const failedJobs = computed(() => jobs.value.filter((job) => job.state === "failed" || job.state === "cancelled"));
const doneJobs = computed(() => jobs.value.filter((job) => job.state === "done"));

const totalSpeed = computed(() =>
  runningJobs.value.reduce((sum, job) => sum + (job.speed && job.speed > 0 ? job.speed : 0), 0),
);

function applyQueueMeta(page: Pick<Page, "queue_paused" | "queue_held" | "queue_message">) {
  paused.value = page.queue_paused;
  held.value = Boolean(page.queue_held);
  pauseMessage.value = page.queue_message;
}

function apply(page: Page) {
  jobs.value = page.items ?? page.downloads ?? [];
  applyQueueMeta(page);
}

const PROGRESS_RESYNC_MIN_MS = 10_000;
let progressResync: Promise<void> | null = null;
let lastProgressResyncAt = 0;

async function resyncDownloadsIfNeeded(incoming: Job[]) {
  if (!shouldResyncDownloadsFromSnapshot(jobs.value, incoming)) {
    return;
  }
  const now = Date.now();
  if (now - lastProgressResyncAt < PROGRESS_RESYNC_MIN_MS) {
    return;
  }
  if (progressResync) {
    return;
  }
  lastProgressResyncAt = now;
  progressResync = (async () => {
    try {
      apply(await api<Page>("/api/v1/downloads"));
    } catch {
      /* garde la liste fusionnée */
    } finally {
      progressResync = null;
    }
  })();
  void progressResync;
}

function origin(job: Job): string {
  return [job.source, job.hoster && job.source && !job.source.includes(job.hoster) ? job.hoster : null, job.release]
    .filter((part): part is string => Boolean(part))
    .join(" · ");
}

function sourceLine(job: Job): string {
  const parts: string[] = [];
  if (job.source) {
    parts.push(job.source);
  }
  if (job.hoster && !parts.some((part) => part.includes(job.hoster!))) {
    parts.push(job.hoster);
  }
  return parts.join(" · ");
}

function formatSpeed(value: number | null): string {
  if (!value) {
    return "—";
  }
  if (value < 1024 * 1024) {
    return `${Math.round(value / 1024)} Ko/s`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} Mo/s`;
}

function formatEta(value: number | null): string {
  if (value == null) {
    return "—";
  }
  const minutes = Math.floor(value / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) {
    return `${hours} h ${minutes % 60} min`;
  }
  if (minutes) {
    return `${minutes} min`;
  }
  return `${value} s`;
}

function sizeLine(job: Job): string {
  if (job.size && job.size > 0) {
    return `${formatBytes(job.done_bytes)} sur ${formatBytes(job.size)}`;
  }
  if (job.done_bytes > 0) {
    return formatBytes(job.done_bytes);
  }
  return "—";
}

function slot(job: Job): string {
  if (job.season == null || job.episode == null) {
    return job.media_title;
  }
  const code = `S${String(job.season).padStart(2, "0")}E${String(job.episode).padStart(2, "0")}`;
  return `${job.media_title} ${code}`;
}

function stepLabel(job: Job): string {
  return job.step || stateOf(job.state).label;
}

function queueHint(job: Job): string {
  return downloadQueueHint(
    job,
    queuedJobs.value.map((row) => row.id),
    runningJobs.value.length > 0,
  );
}

function journalQuery(job: Job): Record<string, string> {
  return { tab: "downloads", download_id: String(job.id) };
}

function openJob(event: MouseEvent, job: Job) {
  const target = event.target as HTMLElement | null;
  if (target?.closest("button, select, a")) {
    return;
  }
  void router.push(`/medias/${job.media_id}`);
}

async function reload() {
  apply(await api<Page>("/api/v1/downloads"));
}

const states: Record<string, { name: string; label: string; spin?: boolean }> = {
  pending: { name: "waiting", label: "En attente" },
  resolving: { name: "progress", label: "Débridage", spin: true },
  queued: { name: "waiting", label: "En file" },
  downloading: { name: "progress", label: "Téléchargement", spin: true },
  post_processing: { name: "progress", label: "Rangement", spin: true },
  failed: { name: "error", label: "En erreur" },
  cancelled: { name: "pause", label: "Annulé" },
};

function stateOf(state: string) {
  return states[state] || { name: "waiting", label: state };
}

async function retry(id: number) {
  error.value = "";
  try {
    await api(`/api/v1/downloads/${id}/retry`, { method: "POST" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Relance impossible.");
  }
}

async function dismiss(id: number) {
  error.value = "";
  try {
    await api(`/api/v1/downloads/${id}`, { method: "DELETE" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Retrait impossible.");
  }
}

async function retryAll(id: number) {
  error.value = "";
  try {
    await api(`/api/v1/downloads/${id}/retry-all`, { method: "POST" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Relance impossible.");
  }
}

async function retryFailed() {
  error.value = "";
  try {
    const body = await api<{ scheduled: number }>("/api/v1/downloads/retry-failed", { method: "POST" });
    error.value = "";
    await reload();
    if (body.scheduled === 0) {
      error.value = "Aucun échec à relancer.";
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Relance impossible.");
  }
}

async function toggleHold() {
  error.value = "";
  try {
    await api(held.value ? "/api/v1/downloads/resume" : "/api/v1/downloads/pause", { method: "POST" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "La file n'a pas pu changer d'état.");
  }
}

async function clearDone() {
  error.value = "";
  try {
    await api("/api/v1/downloads/clear-completed", { method: "POST" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Vidage impossible.");
  }
}

async function cancel(id: number) {
  error.value = "";
  try {
    await api(`/api/v1/downloads/${id}/cancel`, { method: "POST" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Annulation impossible.");
  }
}

function onProgress(page: ProgressPayload) {
  if (document.visibilityState !== "visible") {
    return;
  }
  applyQueueMeta(page);
  const incoming = page.items ?? page.downloads ?? [];
  jobs.value = mergeDownloadJobs(jobs.value, incoming);
  void resyncDownloadsIfNeeded(incoming);
  error.value = "";
}

onMounted(async () => {
  try {
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "File indisponible.");
  }
});

useProgressStream(onProgress);
</script>

<template>
  <section class="downloads-page">
    <header class="downloads-head">
      <div>
        <h1>Téléchargements</h1>
        <p class="hint">Ce qui tourne maintenant, étape par étape.</p>
      </div>
      <p v-if="activeJobs.length || failedJobs.length" class="downloads-summary hint">
        <span v-if="runningJobs.length">{{ runningJobs.length }} actif{{ runningJobs.length > 1 ? "s" : "" }}</span>
        <span v-if="queuedJobs.length">{{ queuedJobs.length }} en file</span>
        <span v-if="failedJobs.length" class="downloads-summary-error"
          >{{ failedJobs.length }} en erreur</span
        >
        <span v-if="totalSpeed">{{ formatSpeed(totalSpeed) }}</span>
      </p>
    </header>

    <p class="actions">
      <button type="button" @click="toggleHold">
        {{ held ? "Reprendre la file" : "Mettre la file en pause" }}
      </button>
    </p>
    <p v-if="paused" class="banner">{{ pauseMessage }}</p>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="!activeJobs.length && !failedJobs.length" class="hint">
      Rien en cours. <router-link to="/suivre">Suivre un titre</router-link>
    </p>

    <section v-if="runningJobs.length" class="downloads-block">
      <h2 class="downloads-section-title">Actifs <span class="hint">{{ runningJobs.length }}</span></h2>
      <ul class="download-jobs-list">
        <li v-for="job in runningJobs" :key="job.id" class="download-job-card" @click="openJob($event, job)">
          <div class="download-job-head">
            <router-link class="download-job-title" :to="`/medias/${job.media_id}`">{{ slot(job) }}</router-link>
            <span class="download-job-progress-label">{{ job.progress }} %</span>
          </div>
          <span class="track download-job-track" aria-hidden="true">
            <span class="fill" :style="{ width: `${job.progress}%` }"></span>
          </span>
          <dl class="download-job-meta">
            <div>
              <dt>Étape</dt>
              <dd>{{ stepLabel(job) }}</dd>
            </div>
            <div>
              <dt>Taille</dt>
              <dd>{{ sizeLine(job) }}</dd>
            </div>
            <div>
              <dt>Vitesse</dt>
              <dd>{{ formatSpeed(job.speed) }}<template v-if="job.eta_seconds"> · reste {{ formatEta(job.eta_seconds) }}</template></dd>
            </div>
            <div v-if="sourceLine(job) || origin(job)">
              <dt>Source</dt>
              <dd>{{ sourceLine(job) || origin(job) }}</dd>
            </div>
            <div v-if="job.attempts > 0">
              <dt>Tentatives</dt>
              <dd>{{ job.attempts }}</dd>
            </div>
          </dl>
          <p v-if="job.error" class="error">{{ job.error }}</p>
          <div class="download-job-actions">
            <router-link class="download-job-journal" :to="{ path: '/journal', query: journalQuery(job) }">
              Voir dans le journal
            </router-link>
            <button
              v-if="job.state !== 'done' && job.state !== 'cancelled' && job.state !== 'failed'"
              type="button"
              class="download-job-cancel"
              @click="cancel(job.id)"
            >
              Annuler
            </button>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="queuedJobs.length" class="downloads-block">
      <h2 class="downloads-section-title">En file <span class="hint">{{ queuedJobs.length }}</span></h2>
      <ul class="download-jobs-list download-jobs-queued">
        <li v-for="job in queuedJobs" :key="job.id" class="download-job-row">
          <div class="download-job-row-copy">
            <strong>{{ slot(job) }}</strong>
            <span class="hint">{{ queueHint(job) }}</span>
          </div>
          <div class="download-job-row-actions">
            <router-link class="download-job-journal" :to="{ path: '/journal', query: journalQuery(job) }">
              Voir dans le journal
            </router-link>
            <button type="button" @click="cancel(job.id)">Annuler</button>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="failedJobs.length" class="downloads-block">
      <h2 class="downloads-section-title icon-line">
        <StatusIcon name="error" label="En erreur" /> En erreur
        <span class="hint">{{ failedJobs.length }}</span>
      </h2>
      <p class="actions">
        <button type="button" class="primary" @click="retryFailed">
          <StatusIcon name="refresh" label="Tout réessayer" /> Tout réessayer
        </button>
      </p>
      <ul class="download-jobs-list">
        <li v-for="job in failedJobs" :key="job.id" class="download-job-card download-job-error">
          <div class="download-job-head">
            <strong>{{ slot(job) }}</strong>
            <span class="error icon-line">
              <StatusIcon :name="stateOf(job.state).name" :label="stateOf(job.state).label" />
            </span>
          </div>
          <p class="error">
            <template v-if="origin(job)">{{ origin(job) }} · </template>
            {{ job.error || stateOf(job.state).label }}
            <span v-if="job.attempts > 0" class="hint"> · {{ job.attempts }} tentative{{ job.attempts > 1 ? "s" : "" }}</span>
          </p>
          <div class="download-job-actions">
            <button type="button" class="primary" @click="retryAll(job.id)">
              <StatusIcon name="refresh" label="Réessayer ce lot" /> Réessayer ce lot
            </button>
            <button v-if="job.state === 'cancelled'" type="button" @click="retry(job.id)">Réessayer</button>
            <router-link class="download-job-journal" :to="{ path: '/journal', query: journalQuery(job) }">
              Voir dans le journal
            </router-link>
            <button type="button" @click="dismiss(job.id)">Retirer de la liste</button>
          </div>
        </li>
      </ul>
    </section>

    <h2>
      <button type="button" @click="showDone = !showDone">Terminés / Archivés ({{ doneJobs.length }})</button>
    </h2>
    <p class="actions">
      <button type="button" :disabled="!doneJobs.length" @click="clearDone">Vider les terminés</button>
    </p>
    <ul v-if="showDone && doneJobs.length" class="entry-list">
      <li v-for="job in doneJobs" :key="job.id">
        <router-link class="entry-main" :to="`/medias/${job.media_id}`">
          <span class="entry-copy">
            <strong>{{ slot(job) }}</strong>
            <span class="hint">
              Terminé<template v-if="origin(job)"> · {{ origin(job) }}</template
              ><template v-if="job.filename"> · {{ job.filename }}</template>. Le fichier reste dans la
              bibliothèque.
            </span>
          </span>
        </router-link>
        <div class="entry-actions">
          <router-link class="download-job-journal" :to="{ path: '/journal', query: journalQuery(job) }">
            Voir dans le journal
          </router-link>
          <button type="button" @click="dismiss(job.id)">Retirer de la liste</button>
        </div>
      </li>
    </ul>
  </section>
</template>

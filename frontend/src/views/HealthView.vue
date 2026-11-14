<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import DiskSpaceCard from "../components/DiskSpaceCard.vue";
import HealthSeriesChart from "../components/HealthSeriesChart.vue";
import MetricSparkline from "../components/MetricSparkline.vue";
import SourceStatesCard from "../components/SourceStatesCard.vue";
import {api, userFacingApiError} from "../api";
import { formatBytes } from "../diskFormat";

interface ComponentRow {
  name: string;
  status: string;
  message: string;
}

interface ProcessMetrics {
  pid: number | null;
  cpu_percent: number | null;
  rss_bytes: number | null;
  write_bytes: number | null;
}

interface MetricsPoint {
  ts: number;
  processes: Record<string, ProcessMetrics>;
  aria2: {
    download_speed: number | null;
    upload_speed: number | null;
    num_active: number | null;
    num_waiting: number | null;
  };
}

interface MetricsBody {
  sampled_at: number;
  interval_s: number;
  instant: MetricsPoint;
  series: MetricsPoint[];
  http: Record<string, { count: number; mean_ms: number | null; p95_ms: number | null }>;
}

interface Dashboard {
  downloads_active: unknown[];
  downloads_failed: unknown[];
}

interface HistoryPointRow {
  bucket_at: string;
  cpu_api: number | null;
  cpu_worker: number | null;
  rss_api: number | null;
  rss_worker: number | null;
}

interface MetricsHistoryBody {
  since: string;
  until: string;
  bucket_minutes: number;
  points: HistoryPointRow[];
}

interface HistorySeries {
  id: string;
  label: string;
  unit: string;
  points: { ts: number; value: number }[];
}

interface DiskUsage {
  volumes: { total_bytes: number; used_bytes: number }[];
}

const POLL_MS = 20_000;

const labels: Record<string, string> = {
  aria2: "aria2",
  alldebrid: "AllDebrid",
  tmdb: "TMDB",
  jellyfin: "Jellyfin",
  wawacity: "Wawacity",
  notifier: "Notifications",
  disk: "Espace disque",
  volumes: "Volumes",
};

const states: Record<string, string> = {
  ok: "en ligne",
  down: "indisponible",
  degraded: "dégradé",
  skipped: "non configuré",
};

const rangeOptions = [
  { id: "1h", label: "1 heure", seconds: 3600 },
  { id: "24h", label: "24 heures", seconds: 86_400 },
  { id: "7d", label: "7 jours", seconds: 604_800 },
] as const;

const rows = ref<ComponentRow[]>([]);
const overall = ref("");
const overallStatus = ref("");
const error = ref("");
const metrics = ref<MetricsBody | null>(null);
const metricsError = ref("");
const range = ref<(typeof rangeOptions)[number]["id"]>("24h");
const activeJobs = ref(0);
const failedJobs = ref(0);
const diskPercent = ref<number | null>(null);
const history = ref<MetricsHistoryBody | null>(null);
const historyError = ref("");

const historySeries = computed((): HistorySeries[] => {
  const body = history.value;
  if (!body) {
    return [];
  }
  const cpu: HistorySeries = { id: "cpu", label: "Processeur", unit: " %", points: [] };
  const memory: HistorySeries = { id: "memory", label: "Mémoire", unit: " Mo", points: [] };
  for (const row of body.points) {
    const ts = Date.parse(row.bucket_at) / 1000;
    if (!Number.isFinite(ts)) {
      continue;
    }
    const apiCpu = row.cpu_api ?? 0;
    const workerCpu = row.cpu_worker ?? 0;
    if (row.cpu_api != null || row.cpu_worker != null) {
      cpu.points.push({ ts, value: Math.round((apiCpu + workerCpu) * 10) / 10 });
    }
    const apiRss = row.rss_api ?? 0;
    const workerRss = row.rss_worker ?? 0;
    if (row.rss_api != null || row.rss_worker != null) {
      memory.points.push({ ts, value: Math.round((apiRss + workerRss) / (1024 * 1024)) });
    }
  }
  return [cpu, memory].filter((series) => series.points.length > 0);
});

let stopped = false;
let timer: ReturnType<typeof setTimeout> | null = null;

const componentRows = computed(() => rows.value.filter((row) => row.name !== "disk" && row.name !== "volumes"));

const filteredSeries = computed(() => {
  const body = metrics.value;
  if (!body) {
    return [] as MetricsPoint[];
  }
  const window = rangeOptions.find((entry) => entry.id === range.value)?.seconds ?? 86_400;
  const cutoff = body.sampled_at - window;
  return body.series.filter((point) => point.ts >= cutoff);
});

function combinedCpu(): (number | null)[] {
  return filteredSeries.value.map((point) => {
    const api = point.processes.api?.cpu_percent;
    const worker = point.processes.worker?.cpu_percent;
    if (api == null && worker == null) {
      return null;
    }
    return (api ?? 0) + (worker ?? 0);
  });
}

function combinedRss(): (number | null)[] {
  return filteredSeries.value.map((point) => {
    const api = point.processes.api?.rss_bytes;
    const worker = point.processes.worker?.rss_bytes;
    if (api == null && worker == null) {
      return null;
    }
    return (api ?? 0) + (worker ?? 0);
  });
}

function writeRatesPerMin(): (number | null)[] {
  const rows = filteredSeries.value;
  const rates: (number | null)[] = [];
  for (let index = 0; index < rows.length; index += 1) {
    if (index === 0) {
      rates.push(null);
      continue;
    }
    const prev = rows[index - 1];
    const cur = rows[index];
    const deltaSeconds = cur.ts - prev.ts;
    if (deltaSeconds <= 0) {
      rates.push(null);
      continue;
    }
    const prevWrite =
      (prev.processes.api?.write_bytes ?? 0) +
      (prev.processes.worker?.write_bytes ?? 0) +
      (prev.processes.aria2?.write_bytes ?? 0);
    const curWrite =
      (cur.processes.api?.write_bytes ?? 0) +
      (cur.processes.worker?.write_bytes ?? 0) +
      (cur.processes.aria2?.write_bytes ?? 0);
    const bytesPerMin = ((curWrite - prevWrite) / deltaSeconds) * 60;
    rates.push(bytesPerMin >= 0 ? bytesPerMin : null);
  }
  return rates;
}

const instantCpu = computed(() => {
  const instant = metrics.value?.instant;
  if (!instant) {
    return null;
  }
  const api = instant.processes.api?.cpu_percent;
  const worker = instant.processes.worker?.cpu_percent;
  if (api == null && worker == null) {
    return null;
  }
  return (api ?? 0) + (worker ?? 0);
});

const instantRss = computed(() => {
  const instant = metrics.value?.instant;
  if (!instant) {
    return null;
  }
  const api = instant.processes.api?.rss_bytes;
  const worker = instant.processes.worker?.rss_bytes;
  if (api == null && worker == null) {
    return null;
  }
  return (api ?? 0) + (worker ?? 0);
});

const instantWriteRate = computed(() => {
  const rates = writeRatesPerMin().filter((value): value is number => value != null);
  return rates.length ? rates[rates.length - 1] : null;
});

const aria2SpeedSeries = computed(() => filteredSeries.value.map((point) => point.aria2.download_speed));

const httpRows = computed(() => {
  const body = metrics.value;
  if (!body) {
    return [];
  }
  return Object.entries(body.http)
    .map(([route, stats]) => ({ route, ...stats }))
    .sort((left, right) => right.count - left.count);
});

const peakCpu = computed(() => {
  const values = combinedCpu().filter((value): value is number => value != null);
  if (!values.length) {
    return null;
  }
  const max = Math.max(...values);
  const index = combinedCpu().findIndex((value) => value === max);
  const point = filteredSeries.value[index];
  return { value: max, at: point?.ts ?? null };
});

function formatPercent(value: number): string {
  return `${Math.round(value * 10) / 10}`;
}

function formatRate(bytesPerMin: number): string {
  return formatBytes(bytesPerMin).replace(" o", "o");
}

function formatMs(value: number | null): string {
  if (value == null) {
    return "—";
  }
  if (value >= 1000) {
    return `${(value / 1000).toFixed(1)} s`;
  }
  return `${Math.round(value)} ms`;
}

function formatClock(ts: number | null): string {
  if (ts == null) {
    return "";
  }
  return new Date(ts * 1000).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function statusClass(status: string): string {
  if (status === "ok") {
    return "health-dot health-dot-ok";
  }
  if (status === "degraded") {
    return "health-dot health-dot-warn";
  }
  if (status === "down") {
    return "health-dot health-dot-danger";
  }
  return "health-dot health-dot-muted";
}

async function loadComponents() {
  const body = await api<{ status: string; components: ComponentRow[] }>("/api/v1/health/components");
  overallStatus.value = body.status;
  overall.value = states[body.status] || body.status;
  rows.value = body.components;
}

async function loadMetrics() {
  metrics.value = await api<MetricsBody>("/api/v1/health/metrics");
}

async function loadHistory() {
  history.value = await api<MetricsHistoryBody>("/api/v1/health/history?hours=168");
}

async function loadStatus() {
  try {
    const board = await api<Dashboard>("/api/v1/dashboard");
    activeJobs.value = board.downloads_active.length;
    failedJobs.value = board.downloads_failed.length;
  } catch {
    activeJobs.value = 0;
    failedJobs.value = 0;
  }
  try {
    const disk = await api<DiskUsage>("/api/v1/health/disk");
    const volume = disk.volumes[0];
    if (volume && volume.total_bytes > 0) {
      diskPercent.value = Math.round((volume.used_bytes / volume.total_bytes) * 100);
    } else {
      diskPercent.value = null;
    }
  } catch {
    diskPercent.value = null;
  }
}

async function refresh() {
  error.value = "";
  metricsError.value = "";
  historyError.value = "";
  try {
    await Promise.all([loadComponents(), loadMetrics(), loadStatus(), loadHistory()]);
  } catch (err) {
    const message = userFacingApiError(err, "Santé indisponible.");
    if (!rows.value.length) {
      error.value = message;
    } else if (!metrics.value) {
      metricsError.value = message;
    } else {
      historyError.value = message;
    }
  }
}

function historyExportUrl(): string {
  return "/api/v1/health/history/export?hours=168";
}

function planPoll() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  if (stopped) {
    return;
  }
  timer = setTimeout(async () => {
    if (document.visibilityState !== "visible") {
      planPoll();
      return;
    }
    await refresh();
    planPoll();
  }, POLL_MS);
}

function onVisible() {
  if (!stopped && document.visibilityState === "visible") {
    void refresh();
  }
}

onMounted(() => {
  void refresh().then(planPoll);
  document.addEventListener("visibilitychange", onVisible);
});

onUnmounted(() => {
  stopped = true;
  if (timer !== null) {
    clearTimeout(timer);
  }
  document.removeEventListener("visibilitychange", onVisible);
});
</script>

<template>
  <section class="stack health-page">
    <header class="health-head">
      <div>
        <h1>Santé</h1>
        <p v-if="overall" class="health-overall">
          <span :class="statusClass(overallStatus)" aria-hidden="true" />
          État général : {{ overall }}.
        </p>
      </div>
      <p class="journal-status hint">
        <span v-if="activeJobs">{{ activeJobs }} job{{ activeJobs > 1 ? "s" : "" }} actif{{ activeJobs > 1 ? "s" : "" }}</span>
        <span v-if="failedJobs" class="journal-status-error">{{ failedJobs }} erreur{{ failedJobs > 1 ? "s" : "" }}</span>
        <span v-if="diskPercent !== null">Disque à {{ diskPercent }} %</span>
      </p>
    </header>

    <p v-if="error" class="error">{{ error }}</p>

    <details class="health-block health-history-panel">
      <summary>
        <h2>Historique (7 jours)</h2>
      </summary>
      <p v-if="history" class="hint">
        Buckets de {{ history.bucket_minutes }} min depuis {{ new Date(history.since).toLocaleString("fr-FR") }}.
      </p>
      <p v-if="historyError" class="error">{{ historyError }}</p>
      <div v-if="historySeries.length" class="health-history-grid">
        <HealthSeriesChart
          v-for="series in historySeries"
          :key="series.id"
          :label="series.label"
          :unit="series.unit"
          :points="series.points"
        />
      </div>
      <p class="actions health-history-actions">
        <a class="button-link" :href="historyExportUrl()" download>Exporter (JSON)</a>
      </p>
    </details>

    <SourceStatesCard />

    <section v-if="componentRows.length" class="health-block">
      <h2>Composants</h2>
      <ul class="health-components">
        <li v-for="row in componentRows" :key="row.name" class="health-component-row">
          <div>
            <strong>{{ labels[row.name] || row.name }}</strong>
            <p class="hint">{{ row.message }}</p>
          </div>
          <span class="health-component-state">
            <span :class="statusClass(row.status)" aria-hidden="true" />
            {{ states[row.status] || row.status }}
          </span>
        </li>
      </ul>
    </section>

    <section class="health-block">
      <DiskSpaceCard heading />
    </section>

    <section class="health-block">
      <h2>Performance</h2>
      <p class="hint">Ce que consomme MediaTracker sur le NAS.</p>
      <p v-if="metricsError" class="error">{{ metricsError }}</p>

      <nav class="journal-tabs health-range" aria-label="Période des métriques">
        <button
          v-for="entry in rangeOptions"
          :key="entry.id"
          type="button"
          :class="{ active: range === entry.id }"
          @click="range = entry.id"
        >
          {{ entry.label }}
        </button>
      </nav>

      <div v-if="metrics" class="health-metrics-grid">
        <article class="panel health-metric-card">
          <h3>Processeur</h3>
          <p class="health-metric-value">{{ instantCpu != null ? `${formatPercent(instantCpu)} %` : "—" }}</p>
          <p v-if="peakCpu" class="hint">pic {{ formatPercent(peakCpu.value) }} % à {{ formatClock(peakCpu.at) }}</p>
          <details class="health-history">
            <summary>Historique</summary>
            <MetricSparkline :values="combinedCpu()" unit=" %" :format-value="formatPercent" />
          </details>
          <dl class="health-process-lines hint">
            <div><dt>API</dt><dd>{{ metrics.instant.processes.api?.cpu_percent ?? "—" }} %</dd></div>
            <div><dt>Worker</dt><dd>{{ metrics.instant.processes.worker?.cpu_percent ?? "—" }} %</dd></div>
            <div><dt>aria2</dt><dd>{{ metrics.instant.processes.aria2?.cpu_percent ?? "—" }} %</dd></div>
          </dl>
        </article>

        <article class="panel health-metric-card">
          <h3>Mémoire</h3>
          <p class="health-metric-value">{{ instantRss != null ? formatBytes(instantRss) : "—" }}</p>
          <p class="hint">API + worker</p>
          <details class="health-history">
            <summary>Historique</summary>
            <MetricSparkline :values="combinedRss()" :format-value="(value) => formatBytes(value)" />
          </details>
          <dl class="health-process-lines hint">
            <div><dt>API</dt><dd>{{ formatBytes(metrics.instant.processes.api?.rss_bytes ?? null) }}</dd></div>
            <div><dt>Worker</dt><dd>{{ formatBytes(metrics.instant.processes.worker?.rss_bytes ?? null) }}</dd></div>
            <div><dt>aria2</dt><dd>{{ formatBytes(metrics.instant.processes.aria2?.rss_bytes ?? null) }}</dd></div>
          </dl>
        </article>

        <article class="panel health-metric-card">
          <h3>Écritures disque</h3>
          <p class="health-metric-value">
            {{ instantWriteRate != null ? `${formatRate(instantWriteRate)}/min` : "—" }}
          </p>
          <p class="hint">débit cumulé API, worker et aria2</p>
          <details class="health-history">
            <summary>Historique</summary>
            <MetricSparkline
              :values="writeRatesPerMin()"
              unit="/min"
              :format-value="(value) => formatRate(value)"
            />
          </details>
        </article>
      </div>

      <article v-if="metrics" class="panel health-aria2">
        <h3>Téléchargements aria2</h3>
        <dl class="health-kv hint">
          <div><dt>Jobs actifs</dt><dd>{{ metrics.instant.aria2.num_active ?? "—" }}</dd></div>
          <div><dt>File d'attente</dt><dd>{{ metrics.instant.aria2.num_waiting ?? "—" }}</dd></div>
          <div>
            <dt>Vitesse cumulée</dt>
            <dd>{{ formatBytes(metrics.instant.aria2.download_speed ?? null) }}/s</dd>
          </div>
        </dl>
        <details class="health-history health-history-block">
          <summary>Historique des débits aria2</summary>
          <MetricSparkline
            :values="aria2SpeedSeries"
            :format-value="(value) => formatBytes(value)"
            unit="/s"
          />
        </details>
      </article>

      <details v-if="httpRows.length" class="health-http">
        <summary>Durées HTTP ({{ httpRows.length }} routes)</summary>
        <table class="health-http-table">
          <thead>
            <tr>
              <th scope="col">Route</th>
              <th scope="col">Nombre</th>
              <th scope="col">Moyenne</th>
              <th scope="col">p95</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in httpRows" :key="row.route">
              <td><code>{{ row.route }}</code></td>
              <td>{{ row.count }}</td>
              <td>{{ formatMs(row.mean_ms) }}</td>
              <td>{{ formatMs(row.p95_ms) }}</td>
            </tr>
          </tbody>
        </table>
      </details>
    </section>

    <p class="actions">
      <button type="button" @click="refresh">Actualiser</button>
    </p>
  </section>
</template>

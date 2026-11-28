<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";

import HealthActivityView from "./HealthActivityView.vue";
import HealthServiceDetail from "../components/HealthServiceDetail.vue";
import HealthSeriesChart from "../components/HealthSeriesChart.vue";
import MetricSparkline from "../components/MetricSparkline.vue";
import SourceStatesCard from "../components/SourceStatesCard.vue";
import { api, userFacingApiError } from "../api";
import { fixedRetentionSnapshot } from "../fixedRetention";
import { formatBytes } from "../diskFormat";
import { useHiveEnabled } from "../composables/useHive";
import type { HivePeerLoadResponse } from "../hive/hivePeerLoad";
import {
  buildHealthServices,
  buildVerdict,
  partitionServices,
  pillClass,
  stateLabel,
  type ComponentRow,
  type SourceTargetRow,
  type UpdateSnapshot,
  type HealthService,
} from "./healthPageServices";

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
    notify_mode?: string | null;
  };
}

interface MetricsBody {
  sampled_at: number;
  interval_s: number;
  instant: MetricsPoint & {
    worker_wakes?: Record<string, number>;
    task_durations?: Record<string, { count: number; last_s: number | null; avg_s: number | null }>;
    source_requests?: Record<string, { count: number; errors: number }>;
  };
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

const POLL_MS = 20_000;

const rangeOptions = [
  { id: "1h", label: "1 heure", seconds: 3600 },
  { id: "24h", label: "24 heures", seconds: 86_400 },
  { id: "7d", label: "7 jours", seconds: 604_800 },
] as const;

const rows = ref<ComponentRow[]>([]);
const sourceTargets = ref<SourceTargetRow[]>([]);
const hosterTargets = ref<SourceTargetRow[]>([]);
const error = ref("");
const metrics = ref<MetricsBody | null>(null);
const metricsError = ref("");
const range = ref<(typeof rangeOptions)[number]["id"]>("24h");
const activeJobs = ref(0);
const failedJobs = ref(0);
const diskPercent = ref<number | null>(null);
const checkedAt = ref<number | null>(null);
const hivePeerLoad = ref<HivePeerLoadResponse | null>(null);
const hivePeerLoadError = ref("");
const updateSnapshot = ref<UpdateSnapshot | null>(null);
const openServiceId = ref<string | null>(null);
const retestingId = ref<string | null>(null);
const refreshing = ref(false);
const history = ref<MetricsHistoryBody | null>(null);
const historyError = ref("");
const techOpen = ref(false);
const healthTab = ref<"etat" | "activite">("etat");

const { hiveEnabled } = useHiveEnabled();

const metricsHistoryDays = computed(() => fixedRetentionSnapshot()?.metrics_history_days ?? null);

const metricsHistoryTitle = computed(() => {
  const days = metricsHistoryDays.value;
  if (days === null) {
    return "Historique";
  }
  return `Historique (${days} jour${days > 1 ? "s" : ""})`;
});

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

const services = computed(() =>
  buildHealthServices({
    components: rows.value,
    sources: sourceTargets.value,
    hiveEnabled: hiveEnabled.value,
    hivePayload: hivePeerLoad.value,
    hiveError: hivePeerLoadError.value,
    update: updateSnapshot.value,
  }),
);

const partitioned = computed(() => partitionServices(services.value));
const problemRows = computed(() => partitioned.value.problems);
const healthyRows = computed(() => partitioned.value.healthy);
const skippedRows = computed(() => partitioned.value.skipped);

const hasProblems = computed(() => problemRows.value.length > 0);

const visiblePrimary = computed(() => (hasProblems.value ? problemRows.value : healthyRows.value));
const collapsedHealthy = computed(() => (hasProblems.value ? healthyRows.value : []));

const verdict = computed(() => buildVerdict(services.value));

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
    const apiCpu = point.processes.api?.cpu_percent;
    const worker = point.processes.worker?.cpu_percent;
    if (apiCpu == null && worker == null) {
      return null;
    }
    return (apiCpu ?? 0) + (worker ?? 0);
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
  const series = filteredSeries.value;
  const rates: (number | null)[] = [];
  for (let index = 0; index < series.length; index += 1) {
    if (index === 0) {
      rates.push(null);
      continue;
    }
    const prev = series[index - 1];
    const cur = series[index];
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

const httpSummary = computed(() => {
  const body = metrics.value;
  if (!body) {
    return "";
  }
  const routes = Object.entries(body.http);
  if (!routes.length) {
    return "";
  }
  let totalCount = 0;
  let meanSum = 0;
  let meanN = 0;
  let p95Max = 0;
  for (const [, stats] of routes) {
    totalCount += stats.count;
    if (stats.mean_ms != null) {
      meanSum += stats.mean_ms;
      meanN += 1;
    }
    if (stats.p95_ms != null) {
      p95Max = Math.max(p95Max, stats.p95_ms);
    }
  }
  const mean = meanN ? Math.round(meanSum / meanN) : null;
  const wakes = metrics.value?.instant.worker_wakes;
  const wakeTotal = wakes ? Object.values(wakes).reduce((acc, n) => acc + n, 0) : 0;
  const sources = metrics.value?.instant.source_requests;
  let srcCount = 0;
  let srcErrors = 0;
  if (sources) {
    for (const row of Object.values(sources)) {
      srcCount += row.count;
      srcErrors += row.errors;
    }
  }
  const parts = [
    `Durées HTTP : ${routes.length} routes`,
    mean != null ? `moyenne ${mean} ms` : "",
    p95Max ? `p95 ${Math.round(p95Max)} ms` : "",
    `réveils du worker : ${wakeTotal}`,
    `requêtes sources : ${srcCount}, dont ${srcErrors} erreur${srcErrors > 1 ? "s" : ""}`,
  ].filter(Boolean);
  return parts.join(". ") + ".";
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

const showTechDetails = computed(
  () => metrics.value != null || history.value != null || historyError.value !== "",
);

const taskHint = computed(() => {
  const durations = metrics.value?.instant.task_durations;
  if (!durations) {
    return "";
  }
  const parts = Object.entries(durations)
    .filter(([, row]) => row.last_s != null)
    .slice(0, 3)
    .map(([name, row]) => `${name.replace(/_/g, " ")} ${Math.round(row.last_s ?? 0)} s`);
  if (!parts.length) {
    return "";
  }
  return `Dernières tâches de fond : ${parts.join(", ")}.`;
});

const measuresSummary = computed(() => {
  const cpu = instantCpu.value != null ? `${Math.round(instantCpu.value)} %` : "—";
  const memory = instantRss.value != null ? formatBytes(instantRss.value) : "—";
  const write = instantWriteRate.value != null ? `${formatRate(instantWriteRate.value)}/min` : "—";
  return `Processeur ${cpu} · Mémoire ${memory} · écritures ${write}`;
});

const verifiedChip = computed(() => {
  const ts = checkedAt.value;
  if (ts == null) {
    return "";
  }
  const label = new Date(ts).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  return `Vérifié à ${label}`;
});

let stopped = false;
let timer: ReturnType<typeof setTimeout> | null = null;

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

function aria2RowHint(): string {
  const row = rows.value.find((entry) => entry.name === "aria2");
  const instant = metrics.value?.instant;
  if (!row) {
    return "";
  }
  if (row.status !== "ok" || !instant) {
    return row.message;
  }
  const active = instant.aria2.num_active;
  const speed = instant.aria2.download_speed;
  const pieces: string[] = [row.message];
  if (active != null) {
    pieces.push(`${active} job${active > 1 ? "s" : ""} actif${active > 1 ? "s" : ""}`);
  }
  if (speed != null && speed > 0) {
    pieces.push(`${formatBytes(speed)}/s`);
  }
  return pieces.join(" · ");
}

function serviceHint(service: HealthService): string {
  if (service.componentName === "aria2") {
    return aria2RowHint();
  }
  return service.message;
}

function toggleService(service: HealthService) {
  openServiceId.value = openServiceId.value === service.id ? null : service.id;
}

function formatCheckedAt(iso: string | null | undefined): string {
  if (!iso) {
    return "";
  }
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) {
    return "";
  }
  return `à ${d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
}

async function loadComponents() {
  const body = await api<{ status: string; components: ComponentRow[] }>("/api/v1/health/components");
  rows.value = body.components;
  checkedAt.value = Date.now();
}

async function loadSources() {
  const body = await api<{ targets: SourceTargetRow[] }>("/api/v1/health/sources");
  sourceTargets.value = body.targets.filter((row) => row.kind === "source");
  hosterTargets.value = body.targets.filter((row) => row.kind === "hoster");
}

async function loadMetrics() {
  metrics.value = await api<MetricsBody>("/api/v1/health/metrics");
}

let historyInFlight: Promise<void> | null = null;

function loadHistory(): Promise<void> {
  if (historyInFlight) {
    return historyInFlight;
  }
  historyInFlight = (async () => {
    try {
      history.value = await api<MetricsHistoryBody>("/api/v1/health/history?hours=168");
      historyError.value = "";
    } catch (err) {
      historyError.value = userFacingApiError(err, "Historique indisponible.");
    } finally {
      historyInFlight = null;
    }
  })();
  return historyInFlight;
}

function onTechToggle(event: Event) {
  techOpen.value = (event.target as HTMLDetailsElement).open;
  if (techOpen.value && !history.value) {
    void loadHistory();
  }
}

async function loadUpdate() {
  let body: {
    detail: string;
    update_available: boolean;
    watchtower_configured: boolean;
    checked_at: string | null;
  };
  try {
    body = await api("/api/v1/ha/update");
  } catch {
    updateSnapshot.value = null;
    return;
  }
  updateSnapshot.value = {
    detail: body.detail,
    updateAvailable: body.update_available,
    watchtowerConfigured: body.watchtower_configured,
    checkedAtLabel: formatCheckedAt(body.checked_at),
  };
}

async function loadHivePeerLoad() {
  if (!hiveEnabled.value) {
    hivePeerLoad.value = null;
    hivePeerLoadError.value = "";
    return;
  }
  try {
    hivePeerLoad.value = await api<HivePeerLoadResponse>("/api/v1/hive/peers/load");
    hivePeerLoadError.value = "";
  } catch (err) {
    hivePeerLoad.value = null;
    hivePeerLoadError.value = userFacingApiError(err, "Charge des pairs indisponible.");
  }
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
    const disk = await api<{ volumes: { total_bytes: number; used_bytes: number }[] }>("/api/v1/health/disk");
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
  refreshing.value = true;
  try {
    await Promise.all([
      loadComponents(),
      loadMetrics(),
      loadStatus(),
      loadSources(),
      loadHivePeerLoad(),
      loadUpdate(),
      techOpen.value ? loadHistory() : Promise.resolve(),
    ]);
  } catch (err) {
    const message = userFacingApiError(err, "Santé indisponible.");
    if (!rows.value.length) {
      error.value = message;
    } else if (!metrics.value) {
      metricsError.value = message;
    } else {
      error.value = message;
    }
  } finally {
    refreshing.value = false;
  }
}

async function retestService(service: HealthService) {
  retestingId.value = service.id;
  try {
    if (service.kind === "component" && service.componentName) {
      const body = await api<{ component: ComponentRow }>(
        `/api/v1/health/components/${encodeURIComponent(service.componentName)}/retest`,
        { method: "POST" },
      );
      const index = rows.value.findIndex((row) => row.name === body.component.name);
      if (index >= 0) {
        const next = rows.value.slice();
        next[index] = body.component;
        rows.value = next;
      }
      if (service.componentName === "alldebrid") {
        await loadSources();
      }
    } else if (service.id === "hive") {
      await loadHivePeerLoad();
    } else if (service.id === "update") {
      await api("/api/v1/ha/actions/check-update", { method: "POST" });
      await loadUpdate();
    } else if (service.kind === "source") {
      await loadSources();
    }
    checkedAt.value = Date.now();
  } catch (err) {
    error.value = userFacingApiError(err, "Nouveau test impossible.");
  } finally {
    retestingId.value = null;
  }
}

function historyExportUrl(): string {
  return "/api/v1/health/history/export?hours=168";
}

function sourceDetailTargets(service: HealthService): SourceTargetRow[] {
  if (service.componentName === "alldebrid") {
    return hosterTargets.value;
  }
  if (service.sourceKey) {
    return sourceTargets.value.filter((row) => row.key === service.sourceKey);
  }
  return [];
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
  <section class="stack health-page sante-page">
    <header class="sante-head">
      <h1>Santé</h1>
      <button v-if="healthTab === 'etat'" type="button" :disabled="refreshing" @click="refresh">
        {{ refreshing ? "Actualisation…" : "Actualiser" }}
      </button>
    </header>

    <nav class="hive-tabs sante-tabs" aria-label="Sections Santé">
      <button type="button" class="hive-tab" :class="{ on: healthTab === 'etat' }" @click="healthTab = 'etat'">État</button>
      <button type="button" class="hive-tab" data-testid="health-tab-activite" :class="{ on: healthTab === 'activite' }" @click="healthTab = 'activite'">Activité</button>
    </nav>

    <HealthActivityView v-if="healthTab === 'activite'" />

    <template v-else>
    <p v-if="error" class="error">{{ error }}</p>

    <article
      class="health-verdict"
      :class="{
        'health-verdict--warn': verdict.rail === 'warn',
        'health-verdict--down': verdict.rail === 'down',
      }"
      aria-label="État général"
    >
      <span class="health-verdict-rail" aria-hidden="true" />
      <div class="health-verdict-body">
        <p class="health-verdict-title">{{ verdict.title }}</p>
        <p v-for="(line, index) in verdict.lines" :key="index" class="health-verdict-copy">{{ line }}</p>
        <div class="health-verdict-meta">
          <span v-if="activeJobs" class="health-chip">{{ activeJobs }} job{{ activeJobs > 1 ? "s" : "" }} actif{{ activeJobs > 1 ? "s" : "" }}</span>
          <span v-else class="health-chip">Aucun job actif</span>
          <span v-if="failedJobs" class="health-chip health-chip-warn">{{ failedJobs }} erreur{{ failedJobs > 1 ? "s" : "" }}</span>
          <span v-if="diskPercent !== null" class="health-chip">Disque à {{ diskPercent }} %</span>
          <span v-if="verifiedChip" class="health-chip">{{ verifiedChip }}</span>
        </div>
      </div>
    </article>

    <h2 class="health-block-title">Services</h2>

    <ul class="health-svc-list">
      <li
        v-for="service in visiblePrimary"
        :key="service.id"
        class="health-svc"
        :class="{ 'health-svc--open': openServiceId === service.id }"
      >
        <button type="button" class="health-svc-row" @click="toggleService(service)">
          <div class="health-svc-copy">
            <strong>{{ service.label }}</strong>
            <p class="hint">{{ serviceHint(service) }}</p>
          </div>
          <span class="health-svc-state">
            <span :class="pillClass(service.status)">{{ stateLabel(service.status, service.id) }}</span>
          </span>
        </button>
        <HealthServiceDetail
          v-if="openServiceId === service.id"
          :service="service"
          :aria2="metrics?.instant.aria2 ?? null"
          :hoster-targets="hosterTargets"
          :source-targets="sourceDetailTargets(service)"
          :hive-peer-load="hivePeerLoad"
          :hive-peer-load-error="hivePeerLoadError"
          :retesting="retestingId === service.id"
          @retest="retestService(service)"
        />
      </li>
    </ul>

    <div v-if="collapsedHealthy.length" class="health-rest">
      <details>
        <summary>{{ collapsedHealthy.length }} autre{{ collapsedHealthy.length > 1 ? "s" : "" }} service{{ collapsedHealthy.length > 1 ? "s" : "" }} en ligne</summary>
        <ul class="health-svc-list">
          <li
            v-for="service in collapsedHealthy"
            :key="service.id"
            class="health-svc"
            :class="{ 'health-svc--open': openServiceId === service.id }"
          >
            <button type="button" class="health-svc-row" @click="toggleService(service)">
              <div class="health-svc-copy">
                <strong>{{ service.label }}</strong>
                <p class="hint">{{ serviceHint(service) }}</p>
              </div>
              <span class="health-svc-state">
                <span :class="pillClass(service.status)">{{ stateLabel(service.status, service.id) }}</span>
              </span>
            </button>
            <HealthServiceDetail
              v-if="openServiceId === service.id"
              :service="service"
              :aria2="metrics?.instant.aria2 ?? null"
              :hoster-targets="hosterTargets"
              :source-targets="sourceDetailTargets(service)"
              :hive-peer-load="hivePeerLoad"
              :hive-peer-load-error="hivePeerLoadError"
              :retesting="retestingId === service.id"
              @retest="retestService(service)"
            />
          </li>
        </ul>
      </details>
    </div>

    <template v-if="skippedRows.length">
      <h2 class="health-block-title health-skipped-title">Non configurés</h2>
      <ul class="health-svc-list health-skipped-list">
        <li v-for="service in skippedRows" :key="service.id" class="health-svc health-svc--skipped">
          <div class="health-svc-row health-svc-row--static">
            <div class="health-svc-copy">
              <strong>{{ service.label }}</strong>
              <p class="hint">{{ service.message }}</p>
            </div>
            <span class="health-svc-state">
              <span :class="pillClass('skipped')">{{ stateLabel("skipped") }}</span>
            </span>
          </div>
        </li>
      </ul>
    </template>

    <details class="health-measures">
      <summary>
        <strong>Mesures</strong>
        <span class="hint">{{ measuresSummary }}</span>
      </summary>
      <div class="health-measures-body">
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
          <article class="health-metric-card">
            <h3>Processeur</h3>
            <p class="health-metric-value">{{ instantCpu != null ? `${formatPercent(instantCpu)} %` : "—" }}</p>
            <p class="hint">
              API {{ metrics.instant.processes.api?.cpu_percent ?? "—" }} % · worker
              {{ metrics.instant.processes.worker?.cpu_percent ?? "—" }} % · aria2
              {{ metrics.instant.processes.aria2?.cpu_percent ?? "—" }} %
            </p>
            <MetricSparkline :values="combinedCpu()" unit=" %" :format-value="formatPercent" />
          </article>
          <article class="health-metric-card">
            <h3>Mémoire</h3>
            <p class="health-metric-value">{{ instantRss != null ? formatBytes(instantRss) : "—" }}</p>
            <p class="hint">API + worker. aria2 {{ formatBytes(metrics.instant.processes.aria2?.rss_bytes ?? null) }}</p>
            <MetricSparkline :values="combinedRss()" :format-value="(value) => formatBytes(value)" />
          </article>
          <article class="health-metric-card">
            <h3>Écritures</h3>
            <p class="health-metric-value">
              {{ instantWriteRate != null ? `${formatRate(instantWriteRate)}/min` : "—" }}
            </p>
            <p class="hint">API, worker et aria2 cumulés</p>
            <MetricSparkline :values="writeRatesPerMin()" unit="/min" :format-value="(value) => formatRate(value)" />
          </article>
        </div>
        <p v-if="taskHint" class="hint">{{ taskHint }}</p>
        <details v-if="showTechDetails" class="health-tech-details" @toggle="onTechToggle">
          <summary>Détails techniques</summary>
          <div class="health-tech-details-body">
            <p v-if="httpSummary" class="hint">{{ httpSummary }}</p>
            <p v-if="metrics?.instant.aria2.notify_mode" class="hint">
              Notifications aria2 : {{ metrics.instant.aria2.notify_mode }}.
            </p>
            <section v-if="history || historyError" class="health-tech-block health-history-panel">
              <h3 class="health-tech-block-title">{{ metricsHistoryTitle }}</h3>
              <p v-if="history" class="hint">
                Buckets de {{ history.bucket_minutes }} min depuis
                {{ new Date(history.since).toLocaleString("fr-FR") }}.
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
            </section>
            <SourceStatesCard v-if="techOpen" class="health-tech-source-card" />
            <section v-if="metrics" class="health-tech-block health-aria2">
              <details class="health-history health-history-block">
                <summary>Historique des débits aria2</summary>
                <MetricSparkline
                  :values="aria2SpeedSeries"
                  :format-value="(value) => formatBytes(value)"
                  unit="/s"
                />
              </details>
            </section>
            <section v-if="httpRows.length" class="health-tech-block health-http-table-wrap">
              <h3 class="health-tech-block-title">Durées HTTP ({{ httpRows.length }} routes)</h3>
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
            </section>
          </div>
        </details>
        <p class="actions health-measures-actions">
          <a class="button-link" :href="historyExportUrl()" download>Exporter l'historique (JSON)</a>
        </p>
      </div>
    </details>
    </template>
  </section>
</template>
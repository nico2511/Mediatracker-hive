<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import {api, userFacingApiError} from "../api";
import { journalDetail } from "../journalMessage";
import { formatParisClock, formatParisDayHeading, parisDayKey } from "../time";

interface JournalEvent {
  id: number;
  ts: string;
  last_seen_at?: string;
  repeat_count?: number;
  type: string;
  level: string;
  media_id: number | null;
  item_id: number | null;
  label: string;
  payload: Record<string, unknown>;
}

interface EventPage {
  items: JournalEvent[];
  next_cursor: string | null;
}

interface MediaOption {
  id: number;
  title: string;
}

interface Dashboard {
  downloads_active: unknown[];
  downloads_failed: unknown[];
}

interface DiskUsage {
  volumes: { total_bytes: number; used_bytes: number }[];
}

type TabId = "all" | "downloads" | "checks" | "cleanup" | "jellyfin" | "errors";

const TABS: { id: TabId; label: string }[] = [
  { id: "all", label: "Tout" },
  { id: "downloads", label: "Téléchargements" },
  { id: "checks", label: "Vérifications" },
  { id: "cleanup", label: "Nettoyage" },
  { id: "jellyfin", label: "Jellyfin" },
  { id: "errors", label: "Erreurs" },
];

const eventJournalRetentionDays = ref<number | null>(null);

const periods = computed(() => {
  const retained = eventJournalRetentionDays.value;
  const allLabel =
    retained != null ? `Tout (${retained} jours conservés)` : "Tout (conservation serveur)";
  return [
    { id: "7d", label: "7 derniers jours", days: 7 },
    { id: "1d", label: "24 heures", days: 1 },
    { id: "30d", label: "30 derniers jours", days: 30 },
    { id: "all", label: allLabel, days: null as number | null },
  ];
});

const LEVELS = [
  { id: "", label: "Tous les niveaux" },
  { id: "info", label: "Info" },
  { id: "warning", label: "Avertissement" },
  { id: "error", label: "Erreur" },
];

const TAB_CATEGORY: Partial<Record<TabId, string>> = {
  downloads: "download.",
  checks: "source.",
  cleanup: "cleanup.",
  jellyfin: "jellyfin.",
};

const route = useRoute();
const router = useRouter();

const tab = ref<TabId>("all");
const level = ref("");
const period = ref("7d");
const mediaId = ref("");
const downloadId = ref("");
const search = ref("");
const items = ref<JournalEvent[]>([]);
const nextCursor = ref<string | null>(null);
const loading = ref(false);
const loadingMore = ref(false);
const error = ref("");
const mediaOptions = ref<MediaOption[]>([]);
const activeJobs = ref(0);
const failedJobs = ref(0);
const diskPercent = ref<number | null>(null);

const todayKey = parisDayKey(new Date().toISOString());
const yesterdayKey = parisDayKey(new Date(Date.now() - 86_400_000).toISOString());

const grouped = computed(() => {
  const groups: { key: string; heading: string; rows: JournalEvent[] }[] = [];
  let currentKey = "";
  for (const row of items.value) {
    const key = parisDayKey(journalWhen(row));
    if (key !== currentKey) {
      currentKey = key;
      groups.push({
        key,
        heading: formatParisDayHeading(key, todayKey, yesterdayKey),
        rows: [],
      });
    }
    groups[groups.length - 1].rows.push(row);
  }
  return groups;
});

function readQuery(): void {
  const query = route.query;
  const rawTab = typeof query.tab === "string" ? query.tab : "all";
  tab.value = TABS.some((row) => row.id === rawTab) ? (rawTab as TabId) : "all";
  level.value = typeof query.level === "string" ? query.level : "";
  const rawPeriod = typeof query.period === "string" ? query.period : "";
  const hasEntityFilter =
    (typeof query.media_id === "string" && query.media_id) ||
    (typeof query.download_id === "string" && query.download_id);
  if (rawPeriod && periods.value.some((row) => row.id === rawPeriod)) {
    period.value = rawPeriod;
  } else if (hasEntityFilter) {
    period.value = "all";
  } else {
    period.value = "7d";
  }
  mediaId.value = typeof query.media_id === "string" ? query.media_id : "";
  downloadId.value = typeof query.download_id === "string" ? query.download_id : "";
  search.value = typeof query.q === "string" ? query.q : "";
}

function sinceIso(): string | undefined {
  const row = periods.value.find((entry) => entry.id === period.value);
  if (!row || row.days === null) {
    return undefined;
  }
  const since = new Date(Date.now() - row.days * 86_400_000);
  return since.toISOString();
}

function queryParams(): Record<string, string> {
  const params: Record<string, string> = {};
  if (tab.value !== "all") {
    params.tab = tab.value;
  }
  if (level.value) {
    params.level = level.value;
  }
  if (period.value !== "7d") {
    params.period = period.value;
  }
  if (mediaId.value) {
    params.media_id = mediaId.value;
  }
  if (downloadId.value) {
    params.download_id = downloadId.value;
  }
  const trimmed = search.value.trim();
  if (trimmed) {
    params.q = trimmed;
  }
  return params;
}

async function syncUrl(): Promise<void> {
  await router.replace({ path: "/journal", query: queryParams() });
}

function apiParams(cursor?: string | null): URLSearchParams {
  const params = new URLSearchParams({ limit: "50" });
  const category = TAB_CATEGORY[tab.value];
  if (category) {
    params.set("category", category);
  }
  if (tab.value === "errors") {
    params.set("level", "error");
  } else if (level.value) {
    params.set("level", level.value);
  }
  const since = sinceIso();
  if (since) {
    params.set("since", since);
  }
  if (mediaId.value) {
    params.set("media_id", mediaId.value);
  }
  if (downloadId.value) {
    params.set("download_id", downloadId.value);
  }
  const trimmed = search.value.trim();
  if (trimmed) {
    params.set("q", trimmed);
  }
  if (cursor) {
    params.set("cursor", cursor);
  }
  return params;
}

async function loadStatus(): Promise<void> {
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

async function loadEventRetentionDays(): Promise<void> {
  try {
    const body = await api<{ event_journal_days: number }>("/api/v1/settings/fixed-retention");
    eventJournalRetentionDays.value = body.event_journal_days;
  } catch {
    eventJournalRetentionDays.value = null;
  }
}

async function loadMediaOptions(): Promise<void> {
  try {
    const body = await api<{ items: MediaOption[] }>("/api/v1/media");
    mediaOptions.value = [...body.items].sort((left, right) => left.title.localeCompare(right.title, "fr"));
  } catch {
    mediaOptions.value = [];
  }
}

async function fetchPage(cursor?: string | null, append = false): Promise<void> {
  const page = await api<EventPage>(`/api/v1/events?${apiParams(cursor)}`);
  items.value = append ? [...items.value, ...page.items] : page.items;
  nextCursor.value = page.next_cursor;
}

async function reload(): Promise<void> {
  loading.value = true;
  error.value = "";
  try {
    await syncUrl();
    await fetchPage(null, false);
  } catch (err) {
    error.value = userFacingApiError(err, "Journal indisponible.");
    if (!items.value.length) {
      items.value = [];
    }
    nextCursor.value = null;
  } finally {
    loading.value = false;
  }
}

async function loadMore(): Promise<void> {
  if (!nextCursor.value || loadingMore.value) {
    return;
  }
  loadingMore.value = true;
  error.value = "";
  try {
    await fetchPage(nextCursor.value, true);
  } catch (err) {
    error.value = userFacingApiError(err, "Chargement impossible.");
  } finally {
    loadingMore.value = false;
  }
}

function mediaTitle(row: JournalEvent): string {
  const title = row.payload.title;
  if (typeof title === "string" && title.trim()) {
    return title.trim();
  }
  const match = mediaOptions.value.find((entry) => entry.id === row.media_id);
  return match?.title || "Fiche";
}

function repeatCount(row: JournalEvent): number {
  const count = row.repeat_count;
  return typeof count === "number" && count > 1 ? count : 1;
}

function journalWhen(row: JournalEvent): string {
  if (repeatCount(row) > 1 && typeof row.last_seen_at === "string" && row.last_seen_at) {
    return row.last_seen_at;
  }
  return row.ts;
}

function levelTone(levelName: string): string {
  if (levelName === "error") {
    return "danger";
  }
  if (levelName === "warning") {
    return "warn";
  }
  if (levelName === "info") {
    return "ok";
  }
  return "muted";
}

function payloadText(payload: Record<string, unknown>): string {
  return JSON.stringify(payload, null, 2);
}

let searchTimer = 0;
let ready = false;

watch(
  () => route.query,
  () => {
    if (!ready) {
      return;
    }
    readQuery();
    void reload();
  },
);

watch([tab, level, period, mediaId, downloadId], () => {
  if (!ready) {
    return;
  }
  void reload();
});

watch(search, () => {
  if (!ready) {
    return;
  }
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    void reload();
  }, 350);
});

onMounted(async () => {
  readQuery();
  await Promise.all([loadStatus(), loadMediaOptions(), loadEventRetentionDays()]);
  await reload();
  ready = true;
});
</script>

<template>
  <section class="stack journal-page">
    <header class="journal-head">
      <div>
        <h1>Journal</h1>
        <p class="hint">Tout ce que MediaTracker a fait ou décidé, avec la raison en clair.</p>
      </div>
      <p class="journal-status hint">
        <span v-if="activeJobs">{{ activeJobs }} job{{ activeJobs > 1 ? "s" : "" }} actif{{ activeJobs > 1 ? "s" : "" }}</span>
        <span v-if="failedJobs" class="journal-status-error">{{ failedJobs }} erreur{{ failedJobs > 1 ? "s" : "" }}</span>
        <span v-if="diskPercent !== null">Disque à {{ diskPercent }} %</span>
      </p>
    </header>

    <nav class="journal-tabs" aria-label="Catégories du journal">
      <button
        v-for="entry in TABS"
        :key="entry.id"
        type="button"
        :class="{ active: tab === entry.id }"
        @click="tab = entry.id"
      >
        {{ entry.label }}
      </button>
    </nav>

    <form class="filters journal-filters" @submit.prevent="reload">
      <label>
        <span>Recherche</span>
        <input v-model="search" type="search" placeholder="Titre ou raison" maxlength="80" />
      </label>
      <label>
        <span>Période</span>
        <select v-model="period">
          <option v-for="entry in periods" :key="entry.id" :value="entry.id">{{ entry.label }}</option>
        </select>
      </label>
      <label>
        <span>Série ou film (Fiche)</span>
        <select v-model="mediaId">
          <option value="">Tous</option>
          <option v-for="entry in mediaOptions" :key="entry.id" :value="String(entry.id)">{{ entry.title }}</option>
        </select>
      </label>
      <label>
        <span>Téléchargement (n°)</span>
        <input v-model="downloadId" inputmode="numeric" placeholder="Tous" />
      </label>
      <label v-if="tab !== 'errors'">
        <span>Niveau</span>
        <select v-model="level">
          <option v-for="entry in LEVELS" :key="entry.id || 'all'" :value="entry.id">{{ entry.label }}</option>
        </select>
      </label>
    </form>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="loading && !items.length" class="hint">Chargement…</p>
    <p v-else-if="!items.length && !error" class="hint">Aucun événement pour ces filtres.</p>

    <div v-for="group in grouped" :key="group.key" class="journal-group">
      <h2 class="journal-day">
        {{ group.heading }}
        <span class="hint">{{ group.rows.length }} événement{{ group.rows.length > 1 ? "s" : "" }}</span>
      </h2>
      <ul class="entry-list journal-list">
        <li v-for="row in group.rows" :key="row.id" class="entry-stack journal-row">
          <div class="journal-row-head">
            <time class="journal-time" :datetime="journalWhen(row)">{{ formatParisClock(journalWhen(row)) }}</time>
            <span class="journal-dot" :class="`tone-${levelTone(row.level)}`" :title="row.level" />
            <div class="journal-copy">
              <p class="journal-title-line">
                <strong>{{ row.label }}</strong>
                <span v-if="repeatCount(row) > 1" class="journal-repeat">×{{ repeatCount(row) }}</span>
                <router-link v-if="row.media_id" :to="`/medias/${row.media_id}`">{{ mediaTitle(row) }}</router-link>
              </p>
              <p v-if="journalDetail(row.type, row.payload)" class="hint journal-detail">
                {{ journalDetail(row.type, row.payload) }}
              </p>
              <details class="journal-tech">
                <summary>Détail technique</summary>
                <pre>{{ payloadText(row.payload) }}</pre>
              </details>
            </div>
          </div>
        </li>
      </ul>
    </div>

    <p v-if="nextCursor" class="journal-more">
      <button type="button" :disabled="loadingMore" @click="loadMore">
        {{ loadingMore ? "Chargement…" : "Plus ancien" }}
      </button>
    </p>
  </section>
</template>

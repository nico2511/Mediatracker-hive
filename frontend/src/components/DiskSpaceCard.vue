<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import {api, userFacingApiError} from "../api";
import { fileSizeLine, folderSizeLine, formatBytes, jobLine } from "../diskFormat";

interface DiskFile {
  name: string;
  written_bytes: number;
  reserved_bytes: number;
  job_id: number | null;
  job_state: string;
  orphan: boolean;
}

interface DiskFolder {
  key: string;
  label: string;
  path: string;
  bytes: number;
  written_bytes?: number;
  reserved_bytes?: number;
  missing: boolean;
  device: string;
  mount: string;
  files?: DiskFile[];
}

interface DiskVolume {
  device: string;
  mount: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  measured_bytes: number;
  other_bytes: number;
  folders: string[];
}

interface DiskUsage {
  computed_at: string | null;
  running: boolean;
  error: string;
  free_bytes: number | null;
  folders: DiskFolder[];
  volumes: DiskVolume[];
}

defineProps<{ heading?: boolean }>();

const report = ref<DiskUsage | null>(null);
const error = ref("");
let timer: ReturnType<typeof setTimeout> | null = null;

onMounted(() => {
  void load();
});

onBeforeUnmount(() => {
  if (timer !== null) {
    clearTimeout(timer);
  }
});

async function load() {
  error.value = "";
  try {
    report.value = await api<DiskUsage>("/api/v1/health/disk");
    planPoll();
  } catch (err) {
    error.value = userFacingApiError(err, "Espace disque indisponible.");
  }
}

async function refresh() {
  error.value = "";
  try {
    report.value = await api<DiskUsage>("/api/v1/health/disk", { method: "POST" });
    planPoll();
  } catch (err) {
    error.value = userFacingApiError(err, "Calcul impossible.");
  }
}

function planPoll() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  if (report.value?.running) {
    timer = setTimeout(() => void load(), 1500);
  }
}

function when(value: string | null): string {
  if (!value) {
    return "pas encore calculé";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

function libraryBytes(folders: DiskFolder[]): number {
  return folders.filter((row) => row.key === "movies" || row.key === "series").reduce((sum, row) => sum + row.bytes, 0);
}

function folderLine(row: DiskFolder): string {
  if (row.missing) {
    return "dossier absent";
  }
  const reserved = row.reserved_bytes ?? 0;
  const written = row.written_bytes ?? row.bytes;
  return folderSizeLine(written, reserved);
}

function incompleteFiles(folders: DiskFolder[]): DiskFile[] {
  return folders.find((row) => row.key === "incomplete")?.files ?? [];
}

function incompleteFolder(folders: DiskFolder[]): DiskFolder | undefined {
  return folders.find((row) => row.key === "incomplete");
}

function fileSize(file: DiskFile): string {
  return fileSizeLine(file.written_bytes, file.reserved_bytes);
}

function gap(volume: DiskVolume): string {
  if (volume.other_bytes > 0) {
    return `${formatBytes(volume.other_bytes)} d'autres données sur ce volume.`;
  }
  if (volume.other_bytes < 0) {
    return "Les dossiers mesurés dépassent l'espace occupé (liens physiques ou autre comptage).";
  }
  return "Rien d'autre de mesuré sur ce volume.";
}
</script>

<template>
  <section class="disk-card" data-disk-card>
    <div class="row">
      <strong v-if="heading">Espace disque</strong>
      <span v-else class="hint">Détail des dossiers</span>
      <button type="button" data-disk-refresh :disabled="report?.running" @click="refresh">
        {{ report?.running ? "Calcul…" : "Actualiser" }}
      </button>
    </div>
    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="report" class="hint" data-disk-status>
      {{ formatBytes(report.free_bytes) }} libres.
      Dernier calcul : {{ when(report.computed_at) }}.
      <template v-if="report.running"> Calcul en cours, l'écran n'attend pas la fin.</template>
      <template v-if="report.error"> {{ report.error }}</template>
    </p>
    <p v-else-if="!error" class="hint">Lecture du cache…</p>
    <template v-if="report && report.folders.length">
      <p class="hint">Bibliothèque {{ formatBytes(libraryBytes(report.folders)) }}</p>
      <dl class="disk-lines">
        <template v-for="row in report.folders" :key="row.key">
          <dt>
            {{ row.label }}
            <span class="hint disk-path">{{ row.path }}</span>
          </dt>
          <dd>{{ folderLine(row) }}</dd>
        </template>
      </dl>
      <p
        v-if="incompleteFolder(report.folders) && incompleteFiles(report.folders).some((file) => file.orphan)"
        class="actions"
      >
        <RouterLink
          class="button"
          data-incomplete-trash-link
          :to="{ path: '/bibliotheque', query: { incomplets: 'corbeille' } }"
        >
          Mettre à la corbeille
        </RouterLink>
      </p>
      <ul v-if="incompleteFiles(report.folders).length" class="disk-files" data-incomplete-files>
        <li v-for="file in incompleteFiles(report.folders)" :key="file.name">
          <span class="disk-file-name">{{ file.name }}</span>
          <span>{{ fileSize(file) }}</span>
          <span class="hint">{{ jobLine(file) }}</span>
        </li>
      </ul>
      <article v-for="volume in report.volumes" :key="`${volume.mount}-${volume.device}`" class="disk-volume">
        <p>
          <strong>Volume {{ volume.mount || "inconnu" }}</strong>
          <span v-if="volume.device" class="hint"> · périphérique {{ volume.device }}</span>
        </p>
        <p class="hint">
          Total {{ formatBytes(volume.total_bytes) }} · utilisé {{ formatBytes(volume.used_bytes) }} · libre
          {{ formatBytes(volume.free_bytes) }}.
        </p>
        <p class="hint">
          Dossiers mesurés ({{ volume.folders.join(", ") }}) : {{ formatBytes(volume.measured_bytes) }}.
          {{ gap(volume) }}
        </p>
      </article>
    </template>
  </section>
</template>

import { onBeforeUnmount, onMounted, ref } from "vue";

import { api, userFacingApiError } from "./api";

export interface DiskFile {
  name: string;
  written_bytes: number;
  reserved_bytes: number;
  job_id: number | null;
  job_state: string;
  orphan: boolean;
}

export interface DiskFolder {
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
  incomplete_trash_count?: number;
  incomplete_kept_recent_count?: number;
  incomplete_keep_until?: string | null;
}

export interface DiskVolume {
  device: string;
  mount: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
  measured_bytes: number;
  other_bytes: number;
  folders: string[];
}

export interface DiskUsage {
  computed_at: string | null;
  running: boolean;
  error: string;
  free_bytes: number | null;
  folders: DiskFolder[];
  volumes: DiskVolume[];
}

const report = ref<DiskUsage | null>(null);
const error = ref("");
let timer: ReturnType<typeof setTimeout> | null = null;
let consumers = 0;

function planPoll() {
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
  if (report.value?.running) {
    timer = setTimeout(() => void load(), 1500);
  }
}

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

function onConsumerMount() {
  consumers += 1;
  if (consumers === 1 && report.value === null && !error.value) {
    void load();
  }
}

function onConsumerUnmount() {
  consumers -= 1;
  if (consumers <= 0) {
    consumers = 0;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }
}

export function useDiskUsage() {
  onMounted(onConsumerMount);
  onBeforeUnmount(onConsumerUnmount);

  return {
    report,
    error,
    load,
    refresh,
  };
}

/** Vide le cache partagé (tests uniquement). */
export function resetDiskUsageForTests() {
  report.value = null;
  error.value = "";
  consumers = 0;
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}
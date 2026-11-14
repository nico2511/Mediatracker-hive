<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

import StatusIcon from "./StatusIcon.vue";
import {api, userFacingApiError} from "../api";
import { formatParis } from "../time";

interface DownloadCard {
  id: number;
  progress: number;
  media_title: string | null;
  season: number | null;
  episode: number | null;
  error: string | null;
}

interface ComponentRow {
  name: string;
  status: string;
  message: string;
}

interface CatalogStatus {
  last_refresh_at: string | null;
  next_refresh_at: string | null;
  last_error: string | null;
}

const props = defineProps<{
  active: DownloadCard[];
  failed: DownloadCard[];
}>();

const healthStatus = ref("");
const healthMessage = ref("");
const diskFreeLabel = ref("");
const catalogStatus = ref<CatalogStatus | null>(null);
const catalogRefreshing = ref(false);
const catalogNotice = ref("");

const activeCount = computed(() => props.active.length);
const failedCount = computed(() => props.failed.length);

const activeSummary = computed(() => {
  if (!activeCount.value) {
    return "aucun en cours";
  }
  const first = props.active[0];
  const progress = first?.progress ?? 0;
  const title = first?.media_title || "Téléchargement";
  const extra = activeCount.value > 1 ? ` (+${activeCount.value - 1})` : "";
  return `${activeCount.value} en cours — ${title} ${progress} %${extra}`;
});

const failedSummary = computed(() => {
  if (!failedCount.value) {
    return "";
  }
  const first = props.failed[0];
  const title = first?.media_title || "Élément";
  const code =
    first?.season != null && first?.episode != null
      ? `S${String(first.season).padStart(2, "0")}E${String(first.episode).padStart(2, "0")}`
      : "";
  const detail = first?.error || "En erreur";
  const extra = failedCount.value > 1 ? ` (+${failedCount.value - 1})` : "";
  return `${failedCount.value} en erreur : ${title}${code ? ` ${code}` : ""} — ${detail}${extra}`;
});

const healthSummary = computed(() => {
  if (!healthStatus.value) {
    return "indisponible";
  }
  const labels: Record<string, string> = {
    ok: "OK",
    degraded: "dégradé",
    down: "en panne",
    unknown: "inconnu",
  };
  const label = labels[healthStatus.value] || healthStatus.value;
  const parts = [label];
  if (healthMessage.value) {
    parts.push(healthMessage.value);
  }
  if (diskFreeLabel.value) {
    parts.push(diskFreeLabel.value);
  }
  return parts.join(" — ");
});

const catalogLine = computed(() => {
  const status = catalogStatus.value;
  if (!status) {
    return "";
  }
  const when = formatParis(status.last_refresh_at) || "jamais";
  const next = status.next_refresh_at ? ` · prochaine ${formatParis(status.next_refresh_at)}` : "";
  const err = status.last_error ? ` · ${status.last_error}` : "";
  return `Catalogue actualisé ${when}${next}${err}`;
});

onMounted(() => {
  void loadMeta();
});

async function loadMeta() {
  try {
    const body = await api<{ status: string; components: ComponentRow[] }>("/api/v1/health/components");
    healthStatus.value = body.status;
    const bad = body.components.find((row) => row.status !== "ok");
    healthMessage.value = bad?.message || "";
  } catch {
    healthStatus.value = "";
  }
  try {
    const disk = await api<{ volumes: { total_bytes: number; used_bytes: number }[] }>("/api/v1/health/disk");
    const volume = disk.volumes[0];
    if (volume && volume.total_bytes > 0) {
      const free = volume.total_bytes - volume.used_bytes;
      const pct = Math.round((volume.used_bytes / volume.total_bytes) * 100);
      const gb = Math.max(0, Math.round(free / (1024 ** 3)));
      diskFreeLabel.value = `disque ${gb} Go libres (${pct} %)`;
    }
  } catch {
    diskFreeLabel.value = "";
  }
  try {
    catalogStatus.value = await api<CatalogStatus>("/api/v1/catalog/status");
  } catch {
    catalogStatus.value = null;
  }
}

async function refreshCatalog() {
  catalogRefreshing.value = true;
  catalogNotice.value = "";
  try {
    const report = await api<{ error: string | null }>("/api/v1/catalog/refresh", { method: "POST" });
    catalogNotice.value = report.error || "Catalogue actualisé.";
    catalogStatus.value = await api<CatalogStatus>("/api/v1/catalog/status");
  } catch (err) {
    catalogNotice.value = userFacingApiError(err, "Actualisation impossible.");
  } finally {
    catalogRefreshing.value = false;
  }
}
</script>

<template>
  <article class="panel home-technical" aria-label="État technique">
    <h2 class="home-technical-title">État technique</h2>
    <ul class="home-technical-list">
      <li>
        <span class="home-technical-copy icon-line">
          <StatusIcon name="progress" label="Téléchargements" />
          Téléchargements : {{ activeSummary }}
        </span>
        <router-link to="/telechargements">Ouvrir la file</router-link>
      </li>
      <li v-if="failedSummary">
        <span class="home-technical-copy error icon-line">
          <StatusIcon name="error" label="Erreur" />
          {{ failedSummary }}
        </span>
        <router-link to="/telechargements">Réessayer</router-link>
      </li>
      <li>
        <span class="home-technical-copy icon-line">
          <StatusIcon name="disk" label="Santé" />
          Santé : {{ healthSummary }}
        </span>
        <router-link to="/sante">Santé</router-link>
      </li>
    </ul>
    <p v-if="catalogLine" class="hint home-technical-catalog">
      {{ catalogLine }}
      <button type="button" class="link-button" :disabled="catalogRefreshing" @click="refreshCatalog">
        {{ catalogRefreshing ? "Actualisation…" : "Actualiser" }}
      </button>
    </p>
    <p v-if="catalogNotice" class="hint">{{ catalogNotice }}</p>
  </article>
</template>

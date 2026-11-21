<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import StatusIcon from "./StatusIcon.vue";
import {api, userFacingApiError} from "../api";

const props = defineProps<{
  mediaId: number;
  mediaType: string;
  seasonNumbers: number[];
  open?: boolean;
}>();

const emit = defineEmits<{
  updated: [];
}>();

interface RepairStep {
  name: string;
  ok: boolean;
  error: string | null;
  detail: string | null;
}

interface RepairResult {
  summary: string;
  detail: string;
  steps: RepairStep[];
}

interface SourceBinding {
  url: string | null;
  path: string | null;
  state: string;
  last_error: string | null;
  manual: boolean;
}

interface SourceTestResult {
  accessible: boolean;
  title: string | null;
  links_by_hoster: Record<string, number>;
  message: string | null;
}

interface DeadLink {
  id: number;
  url: string;
  hoster: string | null;
  season: number | null;
  episode: number | null;
  title: string | null;
  last_error: string | null;
}

const linkUrl = ref("");
const linkSeason = ref<number | null>(null);
const linkEpisode = ref<number | null>(null);
const sourceDraft = ref("");
const selectedSeason = ref<number | null>(null);
const binding = ref<SourceBinding | null>(null);
const deadLinks = ref<DeadLink[]>([]);
const repairReport = ref<RepairResult | null>(null);
const reanalyzeMessage = ref("");
const sourceTest = ref<SourceTestResult | null>(null);
const panelError = ref("");
const panelMessage = ref("");
const busy = ref<string | null>(null);

const stepLabels: Record<string, string> = {
  cross_series: "Épisodes d'autres séries",
  disk: "Fichiers sur disque",
  reanalyze: "Réanalyse bibliothèque",
  source: "Page source",
  jellyfin: "Jellyfin",
  status: "Statut de la fiche",
};

const bindingStates: Record<string, string> = {
  ok: "OK",
  error: "Erreur",
  manual: "Saisie manuelle",
  missing: "Aucune page",
  stale: "À revérifier",
};

const isSeries = computed(() => props.mediaType === "series");

const maintenanceSettingSummary = computed(() => {
  const parts: string[] = [];
  if (deadLinks.value.length) {
    const count = deadLinks.value.length;
    parts.push(`${count} lien mort${count > 1 ? "s" : ""}`);
  }
  if (binding.value) {
    parts.push(bindingStates[binding.value.state] || binding.value.state);
  }
  return parts.length ? parts.join(" · ") : "Liens et réparation";
});

const bindingEndpoint = computed(() => {
  if (!isSeries.value || selectedSeason.value == null) {
    return `/api/v1/media/${props.mediaId}/source-binding`;
  }
  return `/api/v1/media/${props.mediaId}/seasons/${selectedSeason.value}/source-binding`;
});

const testEndpoint = computed(() => {
  if (!isSeries.value || selectedSeason.value == null) {
    return `/api/v1/media/${props.mediaId}/source-binding/test`;
  }
  return `/api/v1/media/${props.mediaId}/seasons/${selectedSeason.value}/source-binding/test`;
});

function slotLabel(season: number | null, episode: number | null): string {
  if (season == null && episode == null) {
    return "Film";
  }
  if (season == null || episode == null) {
    return "Épisode";
  }
  return `S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}`;
}

function hosterSummary(result: SourceTestResult): string {
  const parts = Object.entries(result.links_by_hoster)
    .filter(([, count]) => count > 0)
    .map(([hoster, count]) => `${hoster} (${count})`);
  if (!parts.length) {
    return "Aucun lien hébergeur détecté sur la page.";
  }
  return parts.join(" · ");
}

async function loadDeadLinks() {
  try {
    deadLinks.value = await api<DeadLink[]>(`/api/v1/media/${props.mediaId}/dead-links`);
  } catch {
    deadLinks.value = [];
  }
}

async function loadBinding() {
  binding.value = null;
  sourceDraft.value = "";
  sourceTest.value = null;
  if (isSeries.value && selectedSeason.value == null) {
    return;
  }
  try {
    binding.value = await api<SourceBinding>(bindingEndpoint.value);
    sourceDraft.value = binding.value.url || binding.value.path || "";
  } catch {
    binding.value = null;
    sourceDraft.value = "";
  }
}

async function reloadPanel() {
  await Promise.all([loadBinding(), loadDeadLinks()]);
}

onMounted(async () => {
  if (isSeries.value && props.seasonNumbers.length) {
    selectedSeason.value = props.seasonNumbers[0];
  }
  await reloadPanel();
});

watch(
  () => [props.mediaId, selectedSeason.value],
  async () => {
    await reloadPanel();
  },
);

watch(
  () => props.seasonNumbers,
  (numbers) => {
    if (!isSeries.value || !numbers.length) {
      return;
    }
    if (selectedSeason.value == null || !numbers.includes(selectedSeason.value)) {
      selectedSeason.value = numbers[0];
    }
  },
);

async function runRepair() {
  if (
    !window.confirm(
      "Réparer cette fiche ? Les épisodes et liens d'autres séries seront retirés ; les fichiers restent sur le disque.",
    )
  ) {
    return;
  }
  panelError.value = "";
  panelMessage.value = "";
  repairReport.value = null;
  busy.value = "repair";
  try {
    const result = await api<RepairResult>(`/api/v1/media/${props.mediaId}/repair`, { method: "POST" });
    repairReport.value = result;
    panelMessage.value = result.summary || result.detail;
    emit("updated");
    await loadDeadLinks();
  } catch (err) {
    panelError.value = userFacingApiError(err, "Réparation impossible.");
  } finally {
    busy.value = null;
  }
}

async function runReanalyze() {
  panelError.value = "";
  reanalyzeMessage.value = "";
  busy.value = "reanalyze";
  try {
    const result = await api<{ found: number; new: number; absent: number }>(
      `/api/v1/media/${props.mediaId}/reanalyze`,
      { method: "POST" },
    );
    reanalyzeMessage.value = `${result.found} vu(x), ${result.new} nouveau(x), ${result.absent} marqué(s) absent(s).`;
    emit("updated");
    await loadDeadLinks();
  } catch (err) {
    panelError.value = userFacingApiError(err, "Réanalyse impossible.");
  } finally {
    busy.value = null;
  }
}

async function saveSource() {
  panelError.value = "";
  panelMessage.value = "";
  const value = sourceDraft.value.trim();
  if (!value) {
    panelError.value = "Indiquez l'URL ou le chemin de la page source.";
    return;
  }
  busy.value = "source";
  try {
    const body = value.startsWith("http") ? { url: value } : { path: value };
    binding.value = await api<SourceBinding>(bindingEndpoint.value, {
      method: "PUT",
      body: JSON.stringify(body),
    });
    sourceDraft.value = binding.value.url || binding.value.path || value;
    panelMessage.value = "Page source enregistrée.";
    emit("updated");
  } catch (err) {
    panelError.value = userFacingApiError(err, "Enregistrement impossible.");
  } finally {
    busy.value = null;
  }
}

async function testSource() {
  panelError.value = "";
  sourceTest.value = null;
  busy.value = "test";
  try {
    sourceTest.value = await api<SourceTestResult>(testEndpoint.value, { method: "POST" });
  } catch (err) {
    panelError.value = userFacingApiError(err, "Test impossible.");
  } finally {
    busy.value = null;
  }
}

async function addLink() {
  panelError.value = "";
  panelMessage.value = "";
  const value = linkUrl.value.trim();
  if (!value) {
    panelError.value = "Indiquez l'adresse du lien.";
    return;
  }
  busy.value = "link";
  try {
    const payload: Record<string, unknown> = { media_id: props.mediaId, url: value, start: true };
    if (isSeries.value) {
      const season = linkSeason.value;
      const episode = linkEpisode.value;
      if (typeof season === "number" && !Number.isNaN(season)) {
        payload.season = season;
      }
      if (typeof episode === "number" && !Number.isNaN(episode)) {
        payload.episode = episode;
      }
    }
    const created = await api<{ needs_slot: boolean; source_binding?: boolean }>("/api/v1/links", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    panelMessage.value = created.source_binding
      ? "Page source enregistrée."
      : created.needs_slot
        ? "Lien enregistré. Indiquez la saison et l'épisode sur la fiche si besoin."
        : "Lien ajouté à la file.";
    linkUrl.value = "";
    if (isSeries.value) {
      linkSeason.value = null;
      linkEpisode.value = null;
    }
    emit("updated");
    await reloadPanel();
  } catch (err) {
    panelError.value = userFacingApiError(err, "Lien refusé.");
  } finally {
    busy.value = null;
  }
}
</script>

<template>
  <details class="fiche-setting-details panel fiche-maintenance" :open="open">
    <summary class="fiche-setting-summary">
      <span>Maintenance</span>
      <span class="fiche-setting-value">{{ maintenanceSettingSummary }}</span>
    </summary>

    <div class="fiche-maintenance-block">
      <h3>Réparation</h3>
      <p class="actions">
        <button type="button" class="primary" :disabled="busy !== null" @click="runRepair">
          <StatusIcon name="refresh" label="Réparer" :spin="busy === 'repair'" />
          Réparer
        </button>
        <button type="button" :disabled="busy !== null" @click="runReanalyze">
          <StatusIcon name="search" label="Réanalyser" :spin="busy === 'reanalyze'" />
          Réanalyser
        </button>
      </p>
      <p v-if="reanalyzeMessage" class="hint">{{ reanalyzeMessage }}</p>
      <ul v-if="repairReport?.steps?.length" class="repair-steps">
        <li v-for="step in repairReport.steps" :key="step.name" :class="step.ok ? 'ok' : 'fail'">
          <strong>{{ stepLabels[step.name] || step.name }}</strong>
          <span v-if="step.detail" class="hint"> — {{ step.detail }}</span>
          <span v-if="step.error" class="error"> — {{ step.error }}</span>
        </li>
      </ul>
      <p v-if="repairReport?.detail" class="hint">{{ repairReport.detail }}</p>
    </div>

    <div class="fiche-maintenance-block">
      <h3>Page source</h3>
      <label v-if="isSeries && seasonNumbers.length">
        <span>Saison</span>
        <select v-model.number="selectedSeason">
          <option v-for="number in seasonNumbers" :key="number" :value="number">Saison {{ number }}</option>
        </select>
      </label>
      <p v-if="binding" class="hint">
        État : {{ bindingStates[binding.state] || binding.state }}
        <template v-if="binding.last_error"> · {{ binding.last_error }}</template>
      </p>
      <label>
        <span>URL source</span>
        <input v-model="sourceDraft" class="field-wide" placeholder="https://…/film/…" />
      </label>
      <p class="actions">
        <button type="button" :disabled="busy !== null" @click="saveSource">Enregistrer</button>
        <button type="button" :disabled="busy !== null" @click="testSource">
          <StatusIcon name="search" label="Tester" :spin="busy === 'test'" />
          Tester
        </button>
      </p>
      <p v-if="sourceTest" class="hint" :class="sourceTest.accessible ? 'ok-line' : 'error'">
        <template v-if="sourceTest.accessible">
          Page accessible<template v-if="sourceTest.title"> : {{ sourceTest.title }}</template>.
          {{ hosterSummary(sourceTest) }}
        </template>
        <template v-else>
          Page inaccessible<template v-if="sourceTest.message"> : {{ sourceTest.message }}</template>.
        </template>
      </p>
    </div>

    <div v-if="deadLinks.length" class="fiche-maintenance-block">
      <h3>Liens morts</h3>
      <ul class="dead-links">
        <li v-for="row in deadLinks" :key="row.id">
          <span class="dead-link-slot">{{ slotLabel(row.season, row.episode) }}</span>
          <span class="dead-link-url">{{ row.url }}</span>
          <span v-if="row.hoster" class="hint">{{ row.hoster }}</span>
          <span v-if="row.last_error" class="hint">{{ row.last_error }}</span>
        </li>
      </ul>
    </div>

    <div class="fiche-maintenance-block">
      <h3>Ajouter un lien</h3>
      <form class="add-link-form" @submit.prevent="addLink">
        <p class="hint">
          Collez un lien dl-protect ou un hébergeur (rapidgator, turbobit, nitroflare, katfile, tezfiles).
          Le lien direct AllDebrid n'est pas conservé.
        </p>
        <template v-if="isSeries">
          <label>
            <span>Saison</span>
            <input v-model.number="linkSeason" type="number" min="0" inputmode="numeric" placeholder="1" />
          </label>
          <label>
            <span>Épisode</span>
            <input v-model.number="linkEpisode" type="number" min="0" inputmode="numeric" placeholder="1" />
          </label>
        </template>
        <label>
          <span>Adresse du lien</span>
          <input v-model="linkUrl" required placeholder="https://rapidgator.net/file/…" />
        </label>
        <p v-if="isSeries" class="hint">
          Renseignez la saison et l'épisode pour lancer le téléchargement tout de suite.
        </p>
        <button type="submit" class="primary" :disabled="busy !== null">Télécharger</button>
      </form>
    </div>

    <p v-if="panelError" class="error">{{ panelError }}</p>
    <p v-if="panelMessage" class="hint">{{ panelMessage }}</p>
  </details>
</template>

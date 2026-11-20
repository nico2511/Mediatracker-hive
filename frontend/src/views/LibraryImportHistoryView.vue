<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink } from "vue-router";

import { api, ApiError, userFacingApiError } from "../api";

type ImportMode = "db" | "csv";
type DbStep = "upload" | "user" | "preview" | "done";
type CsvStep = "choose" | "preview" | "done";

interface PreviewLine {
  line: number;
  type: string;
  title: string;
  series_title?: string | null;
  season?: number | null;
  episode?: number | null;
  media_title?: string | null;
  reason?: string | null;
}

interface PreviewBody {
  line_count: number;
  matched_count: number;
  create_count: number;
  create_media_count?: number;
  create_series_count?: number;
  create_film_count?: number;
  unmatched_count: number;
  skipped_count: number;
  identify_pending_count?: number;
  identify_no_token_count?: number;
  identify_no_results_count?: number;
  export_userdata_count?: number;
  export_activity_log_count?: number;
  export_deleted_untitled_count?: number;
  export_message?: string | null;
  export_sure_movies?: number;
  export_sure_episodes?: number;
  export_probable_count?: number;
  export_non_importable_count?: number;
  export_correction_hint?: string | null;
  matched: PreviewLine[];
  to_create: PreviewLine[];
  unmatched: PreviewLine[];
}

interface FollowCandidate {
  id: number;
  title: string;
  year: number | null;
  type: string;
  tmdb_id: number | null;
  followed: boolean;
}

interface ApplyBody extends PreviewBody {
  applied: number;
  created_media: number;
  already_ok: number;
  message: string;
  follow_candidates: FollowCandidate[];
}

interface JellyfinUser {
  id: string;
  username: string;
}

const importMode = ref<ImportMode>("db");
const dbStep = ref<DbStep>("upload");
const csvStep = ref<CsvStep>("choose");

const jellyfinDbFile = ref<File | null>(null);
const backupZipFile = ref<File | null>(null);
const sessionId = ref("");
const users = ref<JellyfinUser[]>([]);
const selectedUserId = ref("");

const csvFile = ref<File | null>(null);
const preview = ref<PreviewBody | null>(null);
const result = ref<ApplyBody | null>(null);
const error = ref("");
const busy = ref(false);
const includeProbable = ref(false);

interface ImportJobProgress {
  phase: string;
  done: number;
  total: number;
  percent: number;
  message?: string | null;
}

const importProgress = ref<ImportJobProgress | null>(null);

async function waitImportJob<T>(jobId: string): Promise<T> {
  for (;;) {
    const status = await api<{
      status: string;
      progress: ImportJobProgress;
      result: T | null;
      error?: { detail?: string; title?: string } | null;
    }>(`/api/v1/library/jellyfin-history/jobs/${jobId}`);
    importProgress.value = status.progress;
    if (status.status === "complete" && status.result) {
      return status.result;
    }
    if (status.status === "error") {
      const detail = status.error?.detail || status.error?.title || "Échec du traitement.";
      throw new ApiError(500, detail);
    }
    await new Promise((resolve) => setTimeout(resolve, 400));
  }
}

const followCandidates = ref<FollowCandidate[]>([]);
const selectedFollow = ref<number[]>([]);
const followMessage = ref("");
const selectAllRef = ref<HTMLInputElement | null>(null);

const jellyfinLabel = computed(() => jellyfinDbFile.value?.name ?? "Aucun fichier choisi");
const backupZipLabel = computed(() => backupZipFile.value?.name ?? "Facultatif — archive Dashboard");
const csvLabel = computed(() => csvFile.value?.name ?? "Aucun fichier choisi");

const selectedUserLabel = computed(() => {
  const row = users.value.find((u) => u.id === selectedUserId.value);
  return row?.username ?? "";
});

const selectableCandidates = computed(() => followCandidates.value.filter((row) => !row.followed));

const allSelected = computed(
  () =>
    selectableCandidates.value.length > 0 &&
    selectableCandidates.value.every((row) => selectedFollow.value.includes(row.id)),
);

const someSelected = computed(
  () =>
    selectableCandidates.value.some((row) => selectedFollow.value.includes(row.id)) && !allSelected.value,
);

watch([allSelected, someSelected], () => {
  const input = selectAllRef.value;
  if (input) {
    input.indeterminate = someSelected.value;
  }
});

async function loadFollowCandidates() {
  try {
    const body = await api<{ items: FollowCandidate[] }>("/api/v1/library/jellyfin-history/follow-candidates");
    followCandidates.value = body.items;
    selectedFollow.value = [];
  } catch {
    followCandidates.value = [];
    selectedFollow.value = [];
  }
}

onMounted(() => {
  void loadFollowCandidates();
});

onBeforeUnmount(() => {
  if (sessionId.value && dbStep.value !== "done") {
    void abandonDbSession();
  }
});

function switchMode(mode: ImportMode) {
  if (mode === importMode.value) {
    return;
  }
  void resetAll();
  importMode.value = mode;
}

async function resetAll() {
  if (sessionId.value && dbStep.value !== "done") {
    await abandonDbSession();
  }
  jellyfinDbFile.value = null;
  backupZipFile.value = null;
  sessionId.value = "";
  users.value = [];
  selectedUserId.value = "";
  dbStep.value = "upload";
  csvFile.value = null;
  preview.value = null;
  result.value = null;
  csvStep.value = "choose";
  includeProbable.value = false;
  error.value = "";
}

function onJellyfinDbChange(event: Event) {
  const input = event.target as HTMLInputElement;
  jellyfinDbFile.value = input.files?.[0] ?? null;
  error.value = "";
}

function onBackupZipChange(event: Event) {
  const input = event.target as HTMLInputElement;
  backupZipFile.value = input.files?.[0] ?? null;
  error.value = "";
}

function onCsvChange(event: Event) {
  const input = event.target as HTMLInputElement;
  csvFile.value = input.files?.[0] ?? null;
  preview.value = null;
  result.value = null;
  error.value = "";
  csvStep.value = "choose";
}

async function abandonDbSession() {
  if (!sessionId.value) {
    return;
  }
  const id = sessionId.value;
  sessionId.value = "";
  try {
    await api(`/api/v1/library/jellyfin-history/db/session/${id}`, { method: "DELETE" });
  } catch {
    /* ignore */
  }
}

async function uploadDatabases() {
  if (!jellyfinDbFile.value && !backupZipFile.value) {
    error.value = "Choisissez jellyfin.db ou une archive de sauvegarde (.zip).";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    if (sessionId.value) {
      await abandonDbSession();
    }
    const form = new FormData();
    if (backupZipFile.value) {
      form.append("backup_zip", backupZipFile.value);
    } else if (jellyfinDbFile.value) {
      form.append("jellyfin_db", jellyfinDbFile.value);
    }
    const body = await api<{ session_id: string }>("/api/v1/library/jellyfin-history/db/session", {
      method: "POST",
      body: form,
    });
    sessionId.value = body.session_id;
    users.value = (
      await api<{ users: JellyfinUser[] }>(
        `/api/v1/library/jellyfin-history/db/session/${body.session_id}/users`,
      )
    ).users;
    selectedUserId.value = users.value.length === 1 ? users.value[0].id : "";
    dbStep.value = "user";
  } catch (err) {
    const base = userFacingApiError(err, "Envoi des bases impossible.");
    if (err instanceof ApiError) {
      const limitHint =
        " Taille maximale par défaut : 1 Go (JELLYFIN_HISTORY_UPLOAD_MAX_BYTES). " +
        "Sur le proxy inverse, vérifiez client_max_body_size.";
      error.value =
        err.status === 413
          ? `${base} (HTTP ${err.status}).${limitHint}`
          : `${base} (HTTP ${err.status}).`;
    } else {
      error.value = base;
    }
  } finally {
    busy.value = false;
  }
}

async function runDbPreview() {
  if (!sessionId.value || !selectedUserId.value) {
    error.value = "Choisissez un compte Jellyfin.";
    return;
  }
  busy.value = true;
  error.value = "";
  importProgress.value = { phase: "starting", done: 0, total: 0, percent: 0, message: "Démarrage…" };
  try {
    const started = await api<{ job_id: string }>(
      `/api/v1/library/jellyfin-history/db/session/${sessionId.value}/preview`,
      {
        method: "POST",
        body: JSON.stringify({
          user_id: selectedUserId.value,
          include_probable: includeProbable.value,
        }),
      },
    );
    preview.value = await waitImportJob<PreviewBody>(started.job_id);
    dbStep.value = "preview";
  } catch (err) {
    error.value = userFacingApiError(err, "Prévisualisation impossible.");
  } finally {
    busy.value = false;
    importProgress.value = null;
  }
}

async function downloadDbCorrectionsCsv() {
  if (!sessionId.value || !selectedUserId.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const match = document.cookie.match(/(?:^|; )mt_csrf=([^;]*)/);
    const token = match ? decodeURIComponent(match[1]) : "";
    const response = await fetch(
      `/api/v1/library/jellyfin-history/db/session/${sessionId.value}/export-corrections.csv`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "text/csv", "X-CSRF-Token": token },
        body: JSON.stringify({ user_id: selectedUserId.value }),
      },
    );
    if (!response.ok) {
      throw new ApiError(response.status, "Téléchargement impossible.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "historique-jellyfin-a-corriger.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    error.value = userFacingApiError(err, "Téléchargement impossible.");
  } finally {
    busy.value = false;
  }
}

async function downloadDbCsv() {
  if (!sessionId.value || !selectedUserId.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const match = document.cookie.match(/(?:^|; )mt_csrf=([^;]*)/);
    const token = match ? decodeURIComponent(match[1]) : "";
    const response = await fetch(
      `/api/v1/library/jellyfin-history/db/session/${sessionId.value}/export.csv`,
      {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "text/csv", "X-CSRF-Token": token },
        body: JSON.stringify({ user_id: selectedUserId.value }),
      },
    );
    if (!response.ok) {
      throw new ApiError(response.status, "Téléchargement impossible.");
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "historique-jellyfin.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    error.value = userFacingApiError(err, "Téléchargement impossible.");
  } finally {
    busy.value = false;
  }
}

async function confirmDbImport() {
  if (!sessionId.value || !selectedUserId.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  importProgress.value = { phase: "starting", done: 0, total: 0, percent: 0, message: "Import…" };
  try {
    const started = await api<{ job_id: string }>(
      `/api/v1/library/jellyfin-history/db/session/${sessionId.value}/import?dry_run=false`,
      {
        method: "POST",
        body: JSON.stringify({
          user_id: selectedUserId.value,
          include_probable: includeProbable.value,
        }),
      },
    );
    result.value = await waitImportJob<ApplyBody>(started.job_id);
    followCandidates.value = result.value.follow_candidates;
    selectedFollow.value = [];
    sessionId.value = "";
    dbStep.value = "done";
  } catch (err) {
    error.value = userFacingApiError(err, "Import impossible.");
  } finally {
    busy.value = false;
    importProgress.value = null;
  }
}

async function runCsvPreview() {
  if (!csvFile.value) {
    error.value = "Choisissez un fichier CSV.";
    return;
  }
  busy.value = true;
  error.value = "";
  importProgress.value = { phase: "starting", done: 0, total: 0, percent: 0, message: "Démarrage…" };
  try {
    const form = new FormData();
    form.append("upload", csvFile.value);
    const started = await api<{ job_id: string }>("/api/v1/library/jellyfin-history/import?dry_run=true", {
      method: "POST",
      body: form,
    });
    preview.value = await waitImportJob<PreviewBody>(started.job_id);
    csvStep.value = "preview";
  } catch (err) {
    error.value = userFacingApiError(err, "Prévisualisation impossible.");
  } finally {
    busy.value = false;
    importProgress.value = null;
  }
}

async function confirmCsvImport() {
  if (!csvFile.value) {
    return;
  }
  busy.value = true;
  error.value = "";
  importProgress.value = { phase: "starting", done: 0, total: 0, percent: 0, message: "Import…" };
  try {
    const form = new FormData();
    form.append("upload", csvFile.value);
    const started = await api<{ job_id: string }>("/api/v1/library/jellyfin-history/import?dry_run=false", {
      method: "POST",
      body: form,
    });
    result.value = await waitImportJob<ApplyBody>(started.job_id);
    followCandidates.value = result.value.follow_candidates;
    selectedFollow.value = [];
    csvStep.value = "done";
  } catch (err) {
    error.value = userFacingApiError(err, "Import impossible.");
  } finally {
    busy.value = false;
    importProgress.value = null;
  }
}

function toggleFollow(id: number) {
  if (selectedFollow.value.includes(id)) {
    selectedFollow.value = selectedFollow.value.filter((value) => value !== id);
  } else {
    selectedFollow.value = [...selectedFollow.value, id];
  }
}

function toggleSelectAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  if (checked) {
    selectedFollow.value = selectableCandidates.value.map((row) => row.id);
  } else {
    selectedFollow.value = [];
  }
}

async function applyFollowSelection() {
  followMessage.value = "";
  busy.value = true;
  try {
    const body = await api<{ updated: number; message: string }>("/api/v1/library/jellyfin-history/refollow", {
      method: "POST",
      body: JSON.stringify({ media_ids: selectedFollow.value }),
    });
    followMessage.value = body.message;
    selectedFollow.value = [];
    await loadFollowCandidates();
  } catch (err) {
    followMessage.value = userFacingApiError(err, "Activation du suivi impossible.");
  } finally {
    busy.value = false;
  }
}

function lineLabel(row: PreviewLine): string {
  if (row.type === "episode" && row.season != null && row.episode != null) {
    const series = row.series_title ? `${row.series_title} — ` : "";
    return `${series}S${String(row.season).padStart(2, "0")}E${String(row.episode).padStart(2, "0")} ${row.title}`;
  }
  return row.title;
}

const showPreviewPanel = computed(
  () => preview.value !== null && (importMode.value === "db" ? dbStep.value === "preview" || dbStep.value === "done" : csvStep.value !== "choose"),
);

const showResultPanel = computed(() => result.value !== null && (dbStep.value === "done" || csvStep.value === "done"));
</script>

<template>
  <section class="stack library-page library-import-page">
    <nav class="library-subnav actions" aria-label="Sous-menu bibliothèque">
      <RouterLink class="button-link" to="/bibliotheque">Retour bibliothèque</RouterLink>
      <span class="library-subnav-current">Importer l'historique</span>
    </nav>

    <header class="library-head">
      <div class="library-head-copy">
        <h1>Importer l'historique Jellyfin</h1>
        <p class="hint library-head-intro">
          Importez directement une copie des bases Jellyfin ou un CSV exporté. Aucune connexion au serveur Jellyfin
          pendant l'import ; le suivi reste désactivé tant que vous ne l'activez pas.
        </p>
      </div>
    </header>

    <div class="actions library-import-mode" role="tablist" aria-label="Mode d'import">
      <button
        type="button"
        :class="{ primary: importMode === 'db' }"
        role="tab"
        :aria-selected="importMode === 'db'"
        data-import-mode-db
        @click="switchMode('db')"
      >
        Bases Jellyfin (recommandé)
      </button>
      <button
        type="button"
        :class="{ primary: importMode === 'csv' }"
        role="tab"
        :aria-selected="importMode === 'csv'"
        data-import-mode-csv
        @click="switchMode('csv')"
      >
        Fichier CSV
      </button>
    </div>

    <template v-if="importMode === 'db'">
      <article v-show="dbStep === 'upload'" class="panel stack" data-import-step-upload>
        <h2 class="library-section-heading">
          <span class="library-section-label">1. Bases Jellyfin</span>
        </h2>
        <p class="hint">
          Copiez <strong>jellyfin.db</strong> ou l'<strong>archive de sauvegarde</strong> (.zip) produite depuis le
          tableau de bord Jellyfin 10.11 (dossier <code>Database/*.json</code> à l'intérieur). Copiez pendant une
          sauvegarde ou avec Jellyfin arrêté pour éviter une base corrompue.
        </p>
        <label class="stack">
          <span>jellyfin.db</span>
          <input type="file" accept=".db,application/octet-stream" data-import-jellyfin-db @change="onJellyfinDbChange" />
          <span class="hint">{{ jellyfinLabel }}</span>
        </label>
        <label class="stack">
          <span>Archive de sauvegarde Jellyfin (.zip)</span>
          <input type="file" accept=".zip,application/zip" data-import-backup-zip @change="onBackupZipChange" />
          <span class="hint">{{ backupZipLabel }}</span>
        </label>
        <div class="actions library-import-actions library-import-upload-actions">
          <p v-if="error && dbStep === 'upload'" class="error library-import-upload-error" role="alert">{{ error }}</p>
          <button
            type="button"
            class="primary"
            :disabled="busy || (!jellyfinDbFile && !backupZipFile)"
            data-import-upload-db
            @click="uploadDatabases"
          >
            Envoyer et continuer
          </button>
        </div>
      </article>

      <article v-show="dbStep === 'user' || dbStep === 'preview' || dbStep === 'done'" class="panel stack" data-import-step-user>
        <h2 class="library-section-heading">
          <span class="library-section-label">2. Compte Jellyfin</span>
        </h2>
        <label v-if="users.length > 1" class="stack">
          <span>Utilisateur</span>
          <select v-model="selectedUserId" data-import-user-select>
            <option disabled value="">Choisir…</option>
            <option v-for="user in users" :key="user.id" :value="user.id">{{ user.username }}</option>
          </select>
        </label>
        <p v-else class="hint">Compte détecté : <strong>{{ selectedUserLabel }}</strong></p>
        <div v-if="dbStep === 'user'" class="actions library-import-actions">
          <button
            type="button"
            class="primary"
            :disabled="busy || !selectedUserId"
            data-import-db-preview
            @click="runDbPreview"
          >
            Prévisualiser
          </button>
          <button type="button" :disabled="busy" @click="resetAll">Abandonner</button>
        </div>
      </article>
    </template>

    <template v-else>
      <article v-show="csvStep === 'choose'" class="panel stack" data-import-step-csv>
        <h2 class="library-section-heading">
          <span class="library-section-label">Import CSV</span>
        </h2>
        <p class="hint">
          CSV produit par
          <code>tools/jellyfin_history_export.py</code>
          (voir
          <a href="https://github.com/nico2511/Mediatracker-hive/blob/main/docs/jellyfin.md#importer-lhistorique--vu--depuis-jellyfin">la doc</a>).
        </p>
        <label class="stack">
          <span>Fichier d'historique</span>
          <input type="file" accept=".csv,text/csv" data-import-csv @change="onCsvChange" />
          <span class="hint">{{ csvLabel }}</span>
        </label>
        <div class="actions library-import-actions">
          <button type="button" class="primary" :disabled="busy || !csvFile" data-import-preview @click="runCsvPreview">
            Prévisualiser
          </button>
        </div>
      </article>
    </template>

    <article
      v-if="importProgress && busy"
      class="panel stack library-import-progress"
      data-import-progress
      aria-live="polite"
    >
      <p class="hint">{{ importProgress.message || "Traitement en cours…" }}</p>
      <div
        class="library-import-progress-bar"
        role="progressbar"
        :aria-valuenow="importProgress.percent"
        aria-valuemin="0"
        aria-valuemax="100"
        :aria-label="importProgress.message || 'Progression import'"
      >
        <i :style="{ width: `${importProgress.percent}%` }" />
      </div>
      <p v-if="importProgress.total > 0" class="hint">
        {{ importProgress.done }} / {{ importProgress.total }} lignes
        <span v-if="importProgress.phase"> · {{ importProgress.phase }}</span>
      </p>
    </article>

    <article v-if="showPreviewPanel && preview" class="panel stack" data-import-step-preview>
      <h2 class="library-section-heading">
        <span class="library-section-label">{{ importMode === 'db' ? '3. Prévisualisation' : 'Prévisualisation' }}</span>
      </h2>
      <p v-if="preview.export_message" class="hint" data-import-export-empty>{{ preview.export_message }}</p>
      <ul v-else class="library-import-stats library-import-preview-grid" data-import-export-stats>
        <li>
          <strong>{{ preview.export_sure_movies ?? 0 }}</strong>
          <span>films sûrs</span>
        </li>
        <li>
          <strong>{{ preview.export_sure_episodes ?? 0 }}</strong>
          <span>épisodes sûrs</span>
        </li>
        <li v-if="(preview.export_probable_count ?? 0) > 0">
          <strong>{{ preview.export_probable_count }}</strong>
          <span>probable(s)</span>
        </li>
        <li v-if="(preview.export_non_importable_count ?? 0) > 0">
          <strong>{{ preview.export_non_importable_count }}</strong>
          <span>non importable(s)</span>
        </li>
        <li class="library-import-preview-break" aria-hidden="true"></li>
        <li>
          <strong>{{ preview.line_count }}</strong>
          <span>lignes</span>
        </li>
        <li>
          <strong>{{ preview.matched_count }}</strong>
          <span>lignes avec fiche existante</span>
        </li>
        <li data-import-create-media>
          <strong>{{ preview.create_media_count ?? 0 }}</strong>
          <span>
            fiches à créer ({{ preview.create_series_count ?? 0 }} séries, {{ preview.create_film_count ?? 0 }} films)
          </span>
        </li>
        <li>
          <strong>{{ preview.create_count }}</strong>
          <span>lignes sans fiche</span>
        </li>
        <li>
          <strong>{{ preview.unmatched_count }}</strong>
          <span>non identifiées</span>
        </li>
        <li v-if="(preview.identify_pending_count ?? 0) > 0">
          <strong>{{ preview.identify_pending_count }}</strong>
          <span>fiche(s) à identifier TMDB</span>
        </li>
        <li v-if="(preview.identify_no_token_count ?? 0) > 0">
          <strong>{{ preview.identify_no_token_count }}</strong>
          <span>sans jeton TMDB (identification impossible)</span>
        </li>
      </ul>
      <p
        v-if="preview.export_correction_hint"
        class="hint library-import-correction-hint"
        data-import-correction-hint
      >
        {{ preview.export_correction_hint }}
      </p>
      <section v-if="preview.unmatched.length" class="stack">
        <h3>Non identifiées</h3>
        <ul class="library-import-lines">
          <li v-for="row in preview.unmatched" :key="row.line">
            Ligne {{ row.line }} — {{ lineLabel(row) }}
            <span v-if="row.reason" class="hint">({{ row.reason }})</span>
          </li>
        </ul>
      </section>
      <label
        v-if="importMode === 'db' && dbStep === 'preview' && (preview.export_probable_count ?? 0) > 0"
        class="library-import-include-probable switch-line"
        data-import-include-probable
      >
        <span>Inclure les probables dans l'import</span>
        <span class="switch">
          <input v-model="includeProbable" type="checkbox" />
          <span class="switch-ui" aria-hidden="true"></span>
        </span>
      </label>
      <p v-if="includeProbable && (preview.export_probable_count ?? 0) > 0" class="hint">
        Les lignes probables seront importées si TMDB permet de les résoudre.
      </p>
      <div
        v-if="importMode === 'db' && dbStep === 'preview'"
        class="actions library-import-actions"
      >
        <button
          v-if="(preview.export_probable_count ?? 0) + (preview.export_non_importable_count ?? 0) > 0"
          type="button"
          :disabled="busy"
          data-import-download-corrections-csv
          @click="downloadDbCorrectionsCsv"
        >
          Télécharger probables / non importables
        </button>
        <button type="button" :disabled="busy" data-import-download-csv @click="downloadDbCsv">
          Télécharger le CSV complet
        </button>
        <button type="button" class="primary" :disabled="busy" data-import-confirm @click="confirmDbImport">
          Confirmer l'import
        </button>
        <button type="button" :disabled="busy" @click="resetAll">Abandonner</button>
      </div>
      <div v-else-if="importMode === 'csv' && csvStep === 'preview'" class="actions">
        <button type="button" class="primary" :disabled="busy" data-import-confirm @click="confirmCsvImport">
          Confirmer l'import
        </button>
        <button type="button" :disabled="busy" @click="resetAll">Recommencer</button>
      </div>
    </article>

    <article v-if="showResultPanel && result" class="panel stack" data-import-step-done>
      <h2 class="library-section-heading">
        <span class="library-section-label">{{ importMode === 'db' ? '4. Résultat' : 'Résultat' }}</span>
      </h2>
      <p>{{ result.message }}</p>
      <p class="hint">
        {{ result.applied }} marquage(s), {{ result.created_media }} fiche(s) créée(s),
        {{ result.already_ok }} déjà à jour.
      </p>
      <div class="actions">
        <button type="button" :disabled="busy" @click="resetAll">Nouvel import</button>
      </div>
    </article>

    <p v-if="error && dbStep !== 'upload'" class="error" role="alert">{{ error }}</p>

    <article
      v-if="followCandidates.length"
      id="reactiver-suivi"
      class="panel stack library-import-refollow-panel"
      data-import-refollow
    >
      <h2 class="library-section-heading">
        <span class="library-section-label">{{ importMode === 'db' ? '5. Réactiver le suivi' : 'Réactiver le suivi' }}</span>
      </h2>
      <p class="hint">
        Titres concernés par le dernier import. Rien n'est suivi tant que vous n'avez pas coché et appliqué.
      </p>
      <label v-if="selectableCandidates.length" class="library-import-select-all check">
        <input
          ref="selectAllRef"
          type="checkbox"
          :checked="allSelected"
          data-import-select-all
          @change="toggleSelectAll"
        />
        <span>Tout sélectionner</span>
      </label>
      <ul class="library-import-refollow">
        <li v-for="row in followCandidates" :key="row.id">
          <label class="check" :class="{ 'library-import-refollow-done': row.followed }">
            <input
              type="checkbox"
              :checked="row.followed ? true : selectedFollow.includes(row.id)"
              :disabled="row.followed || busy"
              @change="toggleFollow(row.id)"
            />
            <span class="library-import-refollow-title">{{ row.title }}</span>
            <span v-if="row.year" class="hint">({{ row.year }})</span>
            <span v-if="row.followed" class="hint"> · Déjà suivi</span>
          </label>
        </li>
      </ul>
      <div class="actions library-import-actions">
        <button
          type="button"
          class="primary"
          :disabled="busy || !selectedFollow.length"
          data-import-refollow-apply
          @click="applyFollowSelection"
        >
          Appliquer le suivi
        </button>
      </div>
      <p v-if="followMessage" class="hint">{{ followMessage }}</p>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api, userFacingApiError, ApiError } from "../api";
import { formatBytes } from "../diskFormat";
import {
  formatParis,
  formatParisClock,
  formatParisDayHeading,
  parisDayKey,
  parseInstant,
} from "../time";
import "./backup-restore.css";

interface BackupRow {
  name: string;
  created_at: string;
  size_bytes: number;
}

interface BackupPreview {
  name: string;
  created_at: string;
  app_version: string | null;
  schema_revision: string | null;
  fiches: number;
  fiches_delta: number;
  restorable: boolean;
  schema_relation: string;
  imported?: boolean;
}

interface RestoreReport {
  ok: boolean;
  backup: string;
  safety_copy: string;
  detail: string;
}

type DialogPhase = "preview" | "running" | "success" | "error-worker" | "error-schema";

const router = useRouter();

const backups = ref<BackupRow[]>([]);
const safetyBackups = ref<BackupRow[]>([]);
const listError = ref("");
const showAll = ref(false);
const showAllSafety = ref(false);

const dialogOpen = ref(false);
const dialogPhase = ref<DialogPhase>("preview");
const dialogSafety = ref(false);
const preview = ref<BackupPreview | null>(null);
const previewSize = ref(0);
const confirmChecked = ref(false);
const restoreError = ref("");
const successReport = ref<RestoreReport | null>(null);
const installedVersion = ref("");
const importBusy = ref(false);
const importError = ref("");
const importInput = ref<HTMLInputElement | null>(null);

const visibleBackups = computed(() => (showAll.value ? backups.value : backups.value.slice(0, 3)));
const visibleSafety = computed(() =>
  showAllSafety.value ? safetyBackups.value : safetyBackups.value.slice(0, 3),
);

const todayKey = computed(() => parisDayKey(new Date().toISOString()));
const yesterdayKey = computed(() => {
  const now = new Date();
  now.setDate(now.getDate() - 1);
  return parisDayKey(now.toISOString());
});

const previewTitleDate = computed(() => {
  if (!preview.value) {
    return "";
  }
  const parsed = parseInstant(preview.value.created_at);
  if (!parsed) {
    return preview.value.name;
  }
  const day = parsed.toLocaleDateString("fr-FR", {
    timeZone: "Europe/Paris",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  return day;
});

const previewLongDate = computed(() => {
  if (!preview.value) {
    return "";
  }
  const parsed = parseInstant(preview.value.created_at);
  if (!parsed) {
    return "";
  }
  const weekday = parsed.toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", weekday: "long" });
  const day = parsed.toLocaleDateString("fr-FR", {
    timeZone: "Europe/Paris",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  const clock = formatParisClock(preview.value.created_at);
  return `${weekday} ${day} à ${clock}`;
});

const currentFiches = computed(() => {
  if (!preview.value) {
    return 0;
  }
  return preview.value.fiches - preview.value.fiches_delta;
});

const fichesDeltaLine = computed(() => {
  if (!preview.value || preview.value.fiches_delta === 0) {
    return "";
  }
  const delta = preview.value.fiches_delta;
  const abs = Math.abs(delta);
  if (delta < 0) {
    return `· ${abs} de moins qu'aujourd'hui (${currentFiches.value})`;
  }
  return `· ${abs} de plus qu'aujourd'hui (${currentFiches.value})`;
});

const schemaBlocked = computed(() => preview.value && !preview.value.restorable);

const isImportedPreview = computed(() => Boolean(preview.value?.imported));

const importKeyWarning = computed(() => {
  if (!isImportedPreview.value) {
    return "";
  }
  return "Clés chiffrées par la SECRET_KEY de l'instance.";
});

const importHiveWarning = computed(() => {
  if (!isImportedPreview.value) {
    return "";
  }
  return "Identité Hive conservée.";
});

const canSubmitRestore = computed(
  () => preview.value?.restorable && confirmChecked.value && dialogPhase.value === "preview",
);

function backupListTitle(createdAt: string): string {
  const key = parisDayKey(createdAt);
  const heading = formatParisDayHeading(key, todayKey.value, yesterdayKey.value);
  const clock = formatParisClock(createdAt);
  if (heading === "Aujourd'hui" || heading === "Hier") {
    return `${heading}, ${clock}`;
  }
  const parsed = parseInstant(createdAt);
  if (!parsed) {
    return formatParis(createdAt);
  }
  const shortDay = parsed
    .toLocaleDateString("fr-FR", { timeZone: "Europe/Paris", weekday: "short" })
    .replace(/\.$/, "");
  const date = parsed.toLocaleDateString("fr-FR", {
    timeZone: "Europe/Paris",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
  return `${shortDay} ${date}, ${clock}`;
}

function isResetBackup(name: string): boolean {
  return name.includes("-reset-");
}

function isImportedBackup(name: string): boolean {
  return name.startsWith("import-");
}

function downloadUrl(row: BackupRow, safety: boolean): string {
  const encoded = encodeURIComponent(row.name);
  return safety
    ? `/api/v1/maintenance/backups/safety/${encoded}/download`
    : `/api/v1/maintenance/backups/${encoded}/download`;
}

async function downloadBackup(row: BackupRow, safety: boolean) {
  listError.value = "";
  try {
    const response = await fetch(downloadUrl(row, safety), { credentials: "include" });
    if (!response.ok) {
      let message = "Téléchargement impossible.";
      try {
        const body = await response.json();
        if (body && typeof body.detail === "string" && body.detail.trim()) {
          message = body.detail;
        }
      } catch {
        /* corps vide */
      }
      listError.value = message;
      return;
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = row.name;
    link.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    if (err instanceof ApiError) {
      listError.value = err.message;
    } else {
      listError.value = userFacingApiError(err, "Téléchargement impossible.");
    }
  }
}

function triggerImport() {
  importError.value = "";
  importInput.value?.click();
}

async function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) {
    return;
  }
  if (!file.name.toLowerCase().endsWith(".db")) {
    importError.value = "Choisissez un fichier .db.";
    return;
  }
  importBusy.value = true;
  importError.value = "";
  try {
    const body = new FormData();
    body.append("file", file);
    const row = await api<BackupRow>("/api/v1/maintenance/backups/import", { method: "POST", body });
    await loadLists();
    await openRestore(row, false);
  } catch (err) {
    importError.value = userFacingApiError(err, "Import refusé.");
  } finally {
    importBusy.value = false;
  }
}

function restorePath(safety: boolean): string {
  const name = preview.value?.name ?? "";
  return safety
    ? `/api/v1/maintenance/backups/safety/${encodeURIComponent(name)}/restore`
    : `/api/v1/maintenance/backups/${encodeURIComponent(name)}/restore`;
}

async function loadLists() {
  listError.value = "";
  try {
    const [main, safety] = await Promise.all([
      api<{ items: BackupRow[] }>("/api/v1/maintenance/backups"),
      api<{ items: BackupRow[] }>("/api/v1/maintenance/backups/safety"),
    ]);
    backups.value = main.items;
    safetyBackups.value = safety.items;
  } catch (err) {
    listError.value = userFacingApiError(err, "Liste des sauvegardes indisponible.");
  }
}

async function loadInstalledVersion() {
  try {
    const health = await api<{ version?: string }>("/api/v1/health");
    installedVersion.value = health.version ?? "";
  } catch {
    installedVersion.value = "";
  }
}

function closeDialog() {
  dialogOpen.value = false;
  preview.value = null;
  confirmChecked.value = false;
  restoreError.value = "";
  successReport.value = null;
  dialogPhase.value = "preview";
}

async function openRestore(row: BackupRow, safety: boolean) {
  dialogSafety.value = safety;
  previewSize.value = row.size_bytes;
  dialogPhase.value = "preview";
  confirmChecked.value = false;
  restoreError.value = "";
  successReport.value = null;
  dialogOpen.value = true;
  try {
    const path = safety
      ? `/api/v1/maintenance/backups/safety/${encodeURIComponent(row.name)}/preview`
      : `/api/v1/maintenance/backups/${encodeURIComponent(row.name)}/preview`;
    preview.value = await api<BackupPreview>(path);
  } catch (err) {
    restoreError.value = userFacingApiError(err, "Aperçu indisponible.");
    dialogPhase.value = "error-schema";
  }
}

async function submitRestore() {
  if (!preview.value || !canSubmitRestore.value) {
    return;
  }
  dialogPhase.value = "running";
  restoreError.value = "";
  try {
    const report = await api<RestoreReport>(restorePath(dialogSafety.value), {
      method: "POST",
      json: { confirm: "RESTORE" },
    });
    successReport.value = report;
    dialogPhase.value = "success";
    await loadLists();
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      dialogPhase.value = "error-worker";
      return;
    }
    if (err instanceof ApiError && err.status === 422) {
      dialogPhase.value = "error-schema";
      restoreError.value = err.message;
      return;
    }
    dialogPhase.value = "error-schema";
    restoreError.value = userFacingApiError(err, "Restauration impossible.");
  }
}

async function reconnect() {
  try {
    await api("/api/v1/auth/logout", { method: "POST" });
  } catch {
    // session may already be invalid after restore
  }
  closeDialog();
  await router.push("/login");
}

function onOverlayKey(event: KeyboardEvent) {
  if (event.key === "Escape" && dialogOpen.value && dialogPhase.value !== "running") {
    closeDialog();
  }
}

watch(dialogOpen, (open) => {
  document.body.style.overflow = open ? "hidden" : "";
});

onMounted(() => {
  void loadLists();
  void loadInstalledVersion();
  document.addEventListener("keydown", onOverlayKey);
});

onBeforeUnmount(() => {
  document.body.style.overflow = "";
  document.removeEventListener("keydown", onOverlayKey);
});
</script>

<template>
  <div class="backup-restore">
    <h3 id="sauvegardes">
      Sauvegardes
      <span v-if="backups.length" class="hint" style="font-weight: 400">{{ backups.length }}</span>
    </h3>
    <p class="hint">
      Restaurer remplace la base actuelle. Celle-ci est d'abord mise de côté dans
      <code>backups/safety/</code>, jamais supprimée.
    </p>
    <div class="import-row">
      <button type="button" class="primary" :disabled="importBusy" @click="triggerImport">
        {{ importBusy ? "Import en cours…" : "Importer une sauvegarde" }}
      </button>
      <span class="hint">Fichier <code>.db</code> — l'aperçu s'ouvre avant toute restauration.</span>
    </div>
    <input
      ref="importInput"
      type="file"
      accept=".db,application/octet-stream"
      class="sr-only"
      @change="onImportFile"
    />
    <p v-if="importError" class="error">{{ importError }}</p>
    <p v-if="listError" class="error">{{ listError }}</p>
    <div v-if="backups.length" class="bklist" role="list">
      <div v-for="row in visibleBackups" :key="row.name" class="bk" role="listitem">
        <div>
          <div class="t">
            {{ backupListTitle(row.created_at) }}
            <span v-if="isResetBackup(row.name)" class="tag">avant réinitialisation</span>
            <span v-if="isImportedBackup(row.name)" class="tag">importée</span>
          </div>
          <div class="m">{{ formatBytes(row.size_bytes) }}</div>
        </div>
        <div class="bk-acts">
          <button
            type="button"
            :aria-label="`Télécharger la sauvegarde du ${backupListTitle(row.created_at)}`"
            @click="downloadBackup(row, false)"
          >
            Télécharger
          </button>
          <button
            type="button"
            :aria-label="`Restaurer la sauvegarde du ${backupListTitle(row.created_at)}`"
            @click="openRestore(row, false)"
          >
            Restaurer…
          </button>
        </div>
      </div>
    </div>
    <p v-else-if="!listError" class="hint">Aucune sauvegarde pour l'instant.</p>
    <p v-if="backups.length > 3" class="show-all">
      <button type="button" class="plain" @click="showAll = !showAll">
        {{ showAll ? "Réduire" : "Afficher tout" }}
      </button>
    </p>

    <details class="backup-restore-safety">
      <summary>
        Copies de sécurité
        <span v-if="safetyBackups.length" class="hint" style="font-weight: 400">{{ safetyBackups.length }}</span>
      </summary>
      <p class="hint">
        Copies automatiques avant chaque restauration. Elles ne sont pas mélangées aux sauvegardes quotidiennes.
      </p>
      <div v-if="safetyBackups.length" class="bklist" role="list">
        <div v-for="row in visibleSafety" :key="row.name" class="bk" role="listitem">
          <div>
            <div class="t">{{ backupListTitle(row.created_at) }}</div>
            <div class="m">{{ formatBytes(row.size_bytes) }}</div>
          </div>
          <div class="bk-acts">
            <button
              type="button"
              :aria-label="`Télécharger la copie de sécurité du ${backupListTitle(row.created_at)}`"
              @click="downloadBackup(row, true)"
            >
              Télécharger
            </button>
            <button
              type="button"
              :aria-label="`Restaurer la copie de sécurité du ${backupListTitle(row.created_at)}`"
              @click="openRestore(row, true)"
            >
              Restaurer…
            </button>
          </div>
        </div>
      </div>
      <p v-else class="hint">Aucune copie de sécurité.</p>
      <p v-if="safetyBackups.length > 3" class="show-all">
        <button type="button" class="plain" @click="showAllSafety = !showAllSafety">
          {{ showAllSafety ? "Réduire" : "Afficher tout" }}
        </button>
      </p>
    </details>
  </div>

  <Teleport to="body">
    <div
      v-if="dialogOpen"
      class="backup-restore-overlay"
      role="presentation"
      @click.self="dialogPhase !== 'running' && closeDialog()"
    >
      <div
        class="backup-restore-dialog"
        role="dialog"
        aria-modal="true"
        :aria-busy="dialogPhase === 'running'"
        :aria-labelledby="dialogPhase === 'success' ? 'restore-success-title' : 'restore-dialog-title'"
      >
        <template v-if="dialogPhase === 'running'">
          <h2 id="restore-dialog-title">Restauration en cours…</h2>
          <p v-if="preview" class="file">{{ preview.name }}</p>
          <div class="prog" role="progressbar" aria-label="Restauration en cours"><i /></div>
          <p class="hint">
            Rien n'est modifié tant que le worker n'a pas confirmé sa pause (1 min au plus). Laissez cette page
            ouverte.
          </p>
        </template>

        <template v-else-if="dialogPhase === 'success' && preview && successReport">
          <p id="restore-success-title" class="big-ok">Base restaurée</p>
          <p>
            La sauvegarde du {{ previewLongDate }} est maintenant la base active : {{ preview.fiches }} fiches.
          </p>
          <p class="hint" style="margin-top: 1rem">
            Reconnectez-vous : la base restaurée ramène le mot de passe, les jetons et la session de l'époque.
          </p>
          <p class="hint">Base précédente mise de côté, jamais supprimée :</p>
          <code class="path">backups/safety/{{ successReport.safety_copy }}</code>
          <div class="acts">
            <button type="button" class="primary" @click="reconnect">Se reconnecter</button>
          </div>
        </template>

        <template v-else-if="dialogPhase === 'error-worker'">
          <p id="restore-dialog-title" class="big-bad warn">Worker occupé</p>
          <p>
            Le worker n'a pas confirmé sa pause dans la minute : il termine sans doute une tâche longue (scan,
            import).
          </p>
          <p class="intact">Base actuelle intacte : rien n'a été modifié.</p>
          <p class="hint">
            Le worker reprend normalement. Réessayez dans quelques minutes ; l'état des jobs est visible dans
            Santé.
          </p>
          <div class="acts">
            <button type="button" class="primary" @click="dialogPhase = 'preview'">Réessayer</button>
            <button type="button" class="plain" @click="closeDialog">Fermer</button>
          </div>
        </template>

        <template v-else-if="dialogPhase === 'error-schema'">
          <p id="restore-dialog-title" class="big-bad">Restauration refusée</p>
          <p v-if="restoreError">{{ restoreError }}</p>
          <p v-else-if="preview">
            Cette sauvegarde (version {{ preview.app_version || "inconnue" }}, schéma
            {{ preview.schema_revision || "inconnu" }}) vient d'une version de MediaTracker plus récente que celle
            installée ({{ installedVersion || "inconnue" }}). Elle ne
            peut pas être ouverte par cette version.
          </p>
          <p class="intact">Base actuelle intacte, worker non interrompu.</p>
          <p class="hint">Mettez l'application à jour (Réglages › Mise à jour et redémarrage), puis recommencez.</p>
          <div class="acts">
            <button type="button" class="primary" @click="closeDialog">Fermer</button>
          </div>
        </template>

        <template v-else-if="preview">
          <h2 id="restore-dialog-title">Restaurer la sauvegarde du {{ previewTitleDate }}</h2>
          <p class="file">{{ preview.name }} · {{ formatBytes(previewSize) }}</p>
          <dl class="disk-lines">
            <dt>Date</dt>
            <dd>{{ previewLongDate }}</dd>
            <dt>Version</dt>
            <dd>
              {{ preview.app_version || "inconnue" }}
              <span v-if="preview.schema_revision" class="hint"> · schéma {{ preview.schema_revision }}</span>
            </dd>
            <dt>Fiches</dt>
            <dd>
              {{ preview.fiches }}
              <span v-if="fichesDeltaLine" class="minus">{{ fichesDeltaLine }}</span>
            </dd>
          </dl>
          <ul class="what">
            <li>
              Tout ce qui a été ajouté ou modifié depuis cette date (fiches, suivis, réglages) disparaît de
              l'application. Les fichiers vidéo sur le disque ne bougent pas.
            </li>
            <li>
              La base actuelle est d'abord copiée dans <code>backups/safety/</code>. Cette copie n'est jamais
              supprimée.
            </li>
            <li>
              Les téléchargements et scans sont mis en pause le temps de l'opération (en général quelques
              secondes, 1 min au plus).
            </li>
          </ul>
          <p v-if="isImportedPreview" class="keyw">
            <span
              ><b>{{ importKeyWarning }}</b> Si cette sauvegarde vient d'une autre instance, les clés (Jellyfin,
              AllDebrid, notifications…) seront illisibles : il faudra les ressaisir.</span
            >
          </p>
          <p v-else class="keyw">
            <span
              ><b>Même SECRET_KEY obligatoire.</b> Les clés des services (Jellyfin, AllDebrid, notifications…)
              sont chiffrées avec la <code>SECRET_KEY</code> du serveur. Si elle a changé depuis cette sauvegarde,
              elles seront illisibles et devront être ressaisies.</span
            >
          </p>
          <p v-if="isImportedPreview" class="hive-keep">
            <span
              ><b>{{ importHiveWarning }}</b> L'import garde l'identité Hive de l'instance qui reçoit, pour ne pas
              faire doublon dans la ruche.</span
            >
          </p>
          <p v-if="schemaBlocked" class="blocked">
            Cette sauvegarde ne peut pas être restaurée : son schéma est plus récent que cette version de
            l'application.
          </p>
          <label class="chk">
            <input v-model="confirmChecked" type="checkbox" :disabled="schemaBlocked" />
            <span>Je remplace la base actuelle par celle du {{ previewTitleDate }}</span>
          </label>
          <div class="acts">
            <button type="button" class="danger" :disabled="!canSubmitRestore" @click="submitRestore">
              Restaurer
            </button>
            <button type="button" class="plain" @click="closeDialog">Annuler</button>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { RouterLink } from "vue-router";

import {
  fileSize,
  folderLine,
  gap,
  incompleteFiles,
  incompleteFolder,
  incompleteTrashEligible,
  jobLine,
  keptRecentLine,
  libraryBytes,
  whenComputed,
} from "../diskUsageFormat";
import { formatBytes } from "../diskFormat";
import { useDiskUsage } from "../useDiskUsage";

withDefaults(
  defineProps<{
    heading?: boolean;
    /** `full` : carte Santé ; `disk` / `incomplete` : sous-blocs Réglages > Disque. */
    panel?: "full" | "disk" | "incomplete";
  }>(),
  { panel: "full" },
);

const { report, error, refresh } = useDiskUsage();
</script>

<template>
  <section
    class="disk-card"
    data-disk-card
    :data-disk-panel="panel"
  >
    <template v-if="panel === 'disk' || panel === 'full'">
      <div class="row">
        <strong v-if="heading && panel === 'full'">Espace disque</strong>
        <span v-else-if="panel === 'disk'" class="hint">Volumes et dossiers MediaTracker</span>
        <span v-else class="hint">Détail des dossiers</span>
        <button type="button" data-disk-refresh :disabled="report?.running" @click="refresh">
          {{ report?.running ? "Calcul…" : "Actualiser" }}
        </button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="report" class="hint" data-disk-status>
        {{ formatBytes(report.free_bytes) }} libres.
        Dernier calcul : {{ whenComputed(report.computed_at) }}.
        <template v-if="report.running"> Calcul en cours, l'écran n'attend pas la fin.</template>
        <template v-if="report.error"> {{ report.error }}</template>
      </p>
      <p v-else-if="!error" class="hint">Lecture du cache…</p>
      <template v-if="report && report.folders.length">
        <article
          v-for="volume in report.volumes"
          :key="`${volume.mount}-${volume.device}`"
          class="disk-volume"
          data-disk-volume
        >
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
        <details v-if="panel === 'disk'" class="library-verify-details" data-disk-folders-details>
          <summary>
            Détail des dossiers
            <span class="hint"> · Bibliothèque {{ formatBytes(libraryBytes(report.folders)) }}</span>
          </summary>
          <dl class="disk-lines">
            <template v-for="row in report.folders" :key="row.key">
              <dt>
                {{ row.label }}
                <span class="hint disk-path">{{ row.path }}</span>
              </dt>
              <dd>{{ folderLine(row) }}</dd>
            </template>
          </dl>
        </details>
        <template v-else>
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
        </template>
      </template>
    </template>

    <template v-if="panel === 'incomplete' || panel === 'full'">
      <template v-if="report && report.folders.length && (panel === 'incomplete' || incompleteFiles(report.folders).length)">
        <p v-if="panel === 'incomplete'" class="hint" data-incomplete-count>
          <template v-if="incompleteFiles(report.folders).length">
            {{ incompleteFiles(report.folders).length }}
            {{ incompleteFiles(report.folders).length === 1 ? "fichier incomplet" : "fichiers incomplets" }}.
          </template>
          <template v-else>Aucun fichier incomplet listé.</template>
        </p>
        <p v-if="incompleteFolder(report.folders) && incompleteTrashEligible(report.folders) > 0" class="actions">
          <RouterLink
            class="button"
            data-incomplete-trash-link
            :to="{ path: '/bibliotheque', query: { incomplets: 'corbeille' } }"
          >
            Mettre à la corbeille
          </RouterLink>
        </p>
        <p
          v-else-if="panel === 'incomplete' && incompleteFolder(report.folders)"
          class="hint"
          data-incomplete-no-trash
        >
          Rien à mettre à la corbeille
          <template v-if="keptRecentLine(report.folders)"> · {{ keptRecentLine(report.folders) }}</template>
        </p>
        <p
          v-else-if="panel === 'full' && incompleteFolder(report.folders) && keptRecentLine(report.folders)"
          class="hint"
          data-incomplete-kept-recent
        >
          {{ keptRecentLine(report.folders) }}
        </p>
        <ul v-if="incompleteFiles(report.folders).length" class="disk-files" data-incomplete-files>
          <li v-for="file in incompleteFiles(report.folders)" :key="file.name">
            <span class="disk-file-name">{{ file.name }}</span>
            <span>{{ fileSize(file) }}</span>
            <span class="hint">{{ jobLine(file) }}</span>
          </li>
        </ul>
      </template>
      <p v-else-if="panel === 'incomplete' && !error" class="hint">Lecture du cache…</p>
    </template>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, toRef } from "vue";

import LineMoreActions from "./LineMoreActions.vue";
import HiveContextMenuItems from "./HiveContextMenuItems.vue";
import ProfileReplacementBanner, { type ProfileReplacementProposal } from "./ProfileReplacementBanner.vue";
import StatusIcon from "./StatusIcon.vue";
import { useProgressStream, type ProgressJob, type ProgressPayload } from "../composables/useProgressStream";

interface ServedProfile {
  profile_id: number;
  profile_name: string;
  repli: boolean;
}

interface FilmItem {
  id: number;
  watched: boolean;
  watch_source?: string;
  downloaded: boolean;
  filename?: string | null;
  original_name?: string | null;
  file_status?: string;
  file_absent?: boolean;
  trash_entry_id?: number | null;
  download_busy?: boolean;
  served_profile?: ServedProfile | null;
  profile_replacement?: ProfileReplacementProposal | null;
}

const props = defineProps<{
  item: FilmItem;
  keep: boolean;
  mediaId: number;
  filmNotYetAvailable?: boolean;
  hiveEnabled?: boolean;
}>();

const emit = defineEmits<{
  markWatched: [undo: boolean];
  setKeep: [value: boolean];
  restore: [];
  redownload: [];
  forceDownload: [];
  deleteFile: [];
  hiveShareChanged: [];
  profileReplacementChanged: [];
}>();

const ACTIVE = new Set(["pending", "resolving", "queued", "downloading", "post_processing"]);
const activeJob = ref<ProgressJob | null>(null);
const vuTipVisible = ref(false);

function applyProgress(payload: ProgressPayload) {
  const jobs = payload.items ?? payload.downloads ?? [];
  const match =
    jobs.find(
      (job) =>
        job.media_id === props.mediaId &&
        ACTIVE.has(job.state) &&
        job.season == null &&
        job.episode == null,
    ) ??
    jobs.find((job) => job.media_id === props.mediaId && ACTIVE.has(job.state));
  activeJob.value = match ?? null;
}

useProgressStream(applyProgress, { mediaId: toRef(props, "mediaId") });

const status = computed(() => props.item.file_status || "absent");

const pillClass = computed(() => {
  if (props.item.download_busy || status.value === "downloading") {
    return "acc";
  }
  switch (status.value) {
    case "present":
      return "ok";
    case "trash":
      return "warn";
    case "absent":
      return "bad";
    default:
      return "acc";
  }
});

const pillLabel = computed(() => {
  if (props.item.download_busy || status.value === "downloading") {
    return "En cours";
  }
  switch (status.value) {
    case "present":
      return "Présent";
    case "trash":
      return "Corbeille";
    case "absent":
      return "Absent";
    default:
      return status.value;
  }
});

function formatEta(value: number | null): string {
  if (value == null) {
    return "";
  }
  const minutes = Math.floor(value / 60);
  const hours = Math.floor(minutes / 60);
  if (hours) {
    return `${hours} h ${minutes % 60} min`;
  }
  if (minutes) {
    return `${minutes} min`;
  }
  return `${value} s`;
}

const progressLine = computed(() => {
  if (!props.item.download_busy && status.value !== "downloading") {
    return "";
  }
  const job = activeJob.value;
  if (!job) {
    return "";
  }
  const eta = formatEta(job.eta_seconds);
  const etaPart = eta ? ` · reste environ ${eta}` : "";
  return `${job.progress} %${etaPart}`;
});

const showRestore = computed(() => Boolean(props.item.trash_entry_id));
const showRedownload = computed(
  () =>
    !props.filmNotYetAvailable &&
    !props.item.trash_entry_id &&
    status.value === "absent" &&
    props.item.file_absent,
);

const watchedPressed = computed(() => props.item.watched);

const showVuUndoTip = computed(() => props.item.watched);

function toggleWatched() {
  emit("markWatched", props.item.watched);
}

function toggleKeep() {
  emit("setKeep", !props.keep);
}
</script>

<template>
  <div class="film-file-block fb">
    <ProfileReplacementBanner
      v-if="item.profile_replacement"
      :item-id="item.id"
      :proposal="item.profile_replacement"
      @changed="emit('profileReplacementChanged')"
    />
    <div class="fb-head">
      <span v-if="item.filename" class="fname">{{ item.filename }}</span>
      <span v-if="!filmNotYetAvailable" class="file-pill" :class="pillClass" role="status">
        <span class="dot" aria-hidden="true"></span>
        {{ pillLabel }}
      </span>
      <button v-if="showRestore" type="button" class="file-ctx-action" @click="emit('restore')">Restaurer</button>
      <button v-else-if="showRedownload" type="button" class="file-ctx-action" @click="emit('redownload')">
        Retélécharger
      </button>
      <span v-if="progressLine" class="file-progress-pct">{{ progressLine }}</span>
    </div>
    <p v-if="item.served_profile?.repli" class="hint file-served-profile">
      Profil utilisé : {{ item.served_profile.profile_name }} (repli)
    </p>
    <p
      v-if="item.original_name && item.original_name !== item.filename"
      class="file-origin orig"
    >
      <span class="lbl">Nom d'origine</span>
      <span class="mono" :title="item.original_name">{{ item.original_name }}</span>
    </p>
    <div class="fb-acts">
      <span
        class="file-toggle-wrap"
        @mouseenter="vuTipVisible = showVuUndoTip"
        @mouseleave="vuTipVisible = false"
        @focusin="vuTipVisible = showVuUndoTip"
        @focusout="vuTipVisible = false"
      >
        <button
          type="button"
          class="file-toggle"
          :class="{ 'is-hover': vuTipVisible }"
          :aria-pressed="watchedPressed ? 'true' : 'false'"
          :title="watchedPressed ? 'Marquer comme non vu' : 'Marquer comme vu'"
          @click="toggleWatched"
        >
          <span class="trk" aria-hidden="true"></span>
          Vu
        </button>
        <span v-if="vuTipVisible" class="file-toggle-tip tip" role="tooltip">Marquer comme non vu</span>
      </span>
      <button
        type="button"
        class="file-toggle"
        :aria-pressed="keep ? 'true' : 'false'"
        @click="toggleKeep"
      >
        <span class="trk" aria-hidden="true"></span>
        <span class="tl">Garder<small> (hors nettoyage)</small></span>
      </button>
      <LineMoreActions
        trigger-variant="toggle"
        compact-on-phone
        menu-aria-label="Plus d'actions sur le fichier"
      >
        <HiveContextMenuItems
          :media-item-id="item.id"
          :media-id="mediaId"
          :hive-enabled="Boolean(hiveEnabled)"
          :versions-to="`/medias/${mediaId}?reglages=ouverts`"
          @changed="emit('hiveShareChanged')"
        />
        <button
          v-if="!filmNotYetAvailable"
          type="button"
          class="action-menu-item"
          role="menuitem"
          data-force-download
          @click="emit('forceDownload')"
        >
          <StatusIcon name="downloaded" label="Forcer" />
          Forcer le téléchargement
        </button>
        <hr v-if="item.downloaded" class="action-menu-separator" />
        <button
          v-if="item.downloaded"
          type="button"
          class="action-menu-item danger"
          role="menuitem"
          @click="emit('deleteFile')"
        >
          <StatusIcon name="trash" label="Supprimer" />
          Supprimer le fichier…
        </button>
      </LineMoreActions>
    </div>
  </div>
</template>

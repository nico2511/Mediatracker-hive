<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import AllDebridAccountBlock from "../../components/AllDebridAccountBlock.vue";
import PasswordFieldWithGenerator from "../../components/PasswordFieldWithGenerator.vue";
import DatabaseBackupRestore from "../../components/DatabaseBackupRestore.vue";
import DiskSpaceCard from "../../components/DiskSpaceCard.vue";
import {api, ApiError, isNetworkError, isTransientHttpStatus, userFacingApiError} from "../../api";
import { fetchSettings, patchSettings } from "../../settings";
import { openInstallPanel, usePwaInstall } from "../../pwa/pwaInstall";
import { formatParisClock, parseInstant } from "../../time";
import {
  formatProfileFallbackOrder,
  parseProfileFallbackOrder,
  PROFILE_FALLBACK_ORDER_KEY,
  qualityProfileSummary,
  type QualityProfileLike,
} from "../../profileFallback";
import { sectionBySlug, SETTINGS_SECTION_SLUGS } from "./sections";

const route = useRoute();
const router = useRouter();
const { deferred: installPrompt } = usePwaInstall();

const section = computed(() => {
  const raw = route.params.section;
  return typeof raw === "string" ? raw : "";
});

const sectionTitle = computed(() => sectionBySlug(section.value)?.title ?? "");

function saveSettingsLabel(fallback = "Enregistrer"): string {
  const title = sectionTitle.value;
  return title ? `Enregistrer les réglages ${title}` : fallback;
}

watch(
  section,
  (slug) => {
    if (!slug || !SETTINGS_SECTION_SLUGS.has(slug)) {
      void router.replace({ name: "settings-index" });
    }
  },
  { immediate: true },
);

const fields = [
  {
    key: "scan_interval_min",
    label: "Intervalle de scan des sources (minutes)",
    hint: "Défaut 720, soit 12 h. Uniquement les séries suivies encore en cours.",
  },
  {
    key: "scan_air_window_days",
    label: "Fenêtre autour de la date de diffusion (jours)",
    hint: "Séries uniquement : une source n'est interrogée que si un épisode est attendu ou vient de sortir.",
  },
  {
    key: "film_theatrical_fallback_months",
    label: "Films sans date numérique TMDB (mois après la sortie en salle)",
    hint: "Estimation de disponibilité quand TMDB ne publie pas encore de date numérique ou physique.",
  },
] as const;

const ongoing = ref(true);
const allowFallback = ref(false);
const fallbackOrderIds = ref<number[]>([]);
const fallbackAddId = ref("");
const fallbackError = ref("");
const fallbackMessage = ref("");
const fallbackSaving = ref(false);
const fallbackPanelStatus = ref<"loading" | "ready" | "error">("loading");
const fallbackPanelLoadError = ref("");
const libraryEnabled = ref(true);
const libraryAutoFollow = ref(false);
const completedAction = ref("archive");
const pushWatched = ref(false);
const autoRename = ref(false);
const mediaPath = ref("");
const moviesPath = ref("");
const seriesPath = ref("");
const downloadsPath = ref("");
const trashPath = ref("");
const storageHint = ref({
  media_dir: "/data/media",
  movies_dir: "/data/media/Films",
  series_dir: "/data/media/Series",
  downloads_dir: "/data/downloads",
  trash_dir: "/data/.trash",
  warning: "",
});
const libraryHours = ref(24);
const values = reactive<Record<string, number>>({
  scan_interval_min: 720,
  source_request_delay_seconds: 5,
  source_error_backoff_min: 180,
  scan_air_window_days: 3,
  film_theatrical_fallback_months: 4,
});
const message = ref("");
const error = ref("");

const jellyfinUrl = ref("");
const jellyfinKey = ref("");
const jellyfinKeySet = ref(false);
const jellyfinUser = ref("");
const jellyfinUsers = ref<{ id: string; name: string }[]>([]);
const jellyfinPrefix = ref("");
const jellyfinMap = ref("");
const jellyfinPoll = ref(30);
const webhookUrl = ref("");
const webhookTokenHeader = ref("X-MediaTracker-Token");
const webhookTokenSet = ref(false);
const webhookTokenPreview = ref("");
const webhookTokenRevealed = ref("");
const jellyfinMessage = ref("");
const jellyfinError = ref("");

interface NotifyEvent {
  type: string;
  label: string;
  default_on?: boolean;
}

interface WebhookTarget {
  id: number;
  name: string;
  url: string;
  type: string;
  events: string[];
  enabled: boolean;
  failure_count: number;
}

interface PreviewFile {
  media_id: number;
  title: string;
  season: number | null;
  episode: number | null;
  bytes: number;
  delete_at: string;
}

interface TrashItem {
  id: number;
  label: string;
  size: number;
  reason: string;
  purge_after: string;
}

const publicBase = ref("");
const groupSeconds = ref(120);
const quietStart = ref("");
const quietEnd = ref("");
const autoDeleteEnabled = ref(true);
const incompleteCleanup = ref(false);
const autoDays = ref(14);
const noticeHours = ref(24);
const trashDays = ref(7);
const notifyMessage = ref("");
const notifyError = ref("");
const targetNotes = ref<Record<number, { ok: boolean; message: string }>>({});
const cleanupMessage = ref("");
const cleanupError = ref("");
const cleanupMigrationBanner = ref("");
const notifyEvents = ref<NotifyEvent[]>([]);
const targets = ref<WebhookTarget[]>([]);
const targetName = ref("Home Assistant");
const targetType = ref("homeassistant");
const haHost = ref("http://homeassistant.local:8123");
const haId = ref("mediatracker");
const ntfyUrl = ref("");
const ntfyToken = ref("");
const genericUrl = ref("");
const genericSecret = ref("");
const selectedEvents = ref<string[]>([]);
const editingTargetId = ref<number | null>(null);
const previewDue = ref<PreviewFile[]>([]);
const previewUpcoming = ref<PreviewFile[]>([]);
const trashItems = ref<TrashItem[]>([]);
const trashBytes = ref(0);
const reclaimedBytes = ref(0);
const diskMinGb = ref(5);
const backupKeep = ref(7);
const alldebridAutoPurge = ref(true);
const autoDownloadDefault = ref(true);
const alldebridMessage = ref("");
const alldebridError = ref("");
const diskMessage = ref("");
const diskError = ref("");

interface ApiTokenRow {
  id: number;
  name: string;
  prefix: string;
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
}

const tokens = ref<ApiTokenRow[]>([]);
const tokenName = ref("");
const tokenScopes = ref<string[]>(["read"]);
const freshToken = ref("");
const tokenError = ref("");
const accountCurrentPassword = ref("");
const accountNewPassword = ref("");
const accountError = ref("");
const accountMessage = ref("");
const accountBusy = ref(false);
const resetConfirm = ref("");
const resetError = ref("");
const resetMessage = ref("");
const resetting = ref(false);

interface QualityProfileRow {
  id: number;
  name: string;
  languages: string[];
  min_resolution: string | null;
  max_resolution: string | null;
  min_size_mb: number | null;
  max_size_mb: number | null;
  exclude_keywords: string[];
  is_default: boolean;
}

const profiles = ref<QualityProfileRow[]>([]);
const profileFormRef = ref<HTMLElement | null>(null);

const profileFormHeading = computed(() => {
  if (profileId.value == null) {
    return "Nouveau profil";
  }
  const name = profileName.value.trim();
  return name ? `Modifier le profil « ${name} »` : "Modifier le profil";
});
const profileError = ref("");
const profileMessage = ref("");
const profileId = ref<number | null>(null);
const profileName = ref("");
const profileLanguages = ref("VFF, VFQ, VF, MULTI, VOSTFR");
const profileMinResolution = ref("");
const profileMaxResolution = ref("1080p");
const profileMinSize = ref("");
const profileMaxSize = ref("");
const profileKeywords = ref("");
const resolutions = ["", "SD", "HDLight", "HD", "HDLight1080", "1080p", "4K"];

const bootstrapDone = ref(false);
const loadedSections = ref(new Set<string>());

async function applySettingsBody(body: Awaited<ReturnType<typeof fetchSettings>>) {
  for (const row of body.settings) {
    if (row.key in values && typeof row.value === "number") {
      values[row.key] = row.value;
    }
    if (row.key === "scan_ongoing_only" && typeof row.value === "boolean") {
      ongoing.value = row.value;
    }
    if (row.key === "library_scan_enabled" && typeof row.value === "boolean") {
      libraryEnabled.value = row.value;
    }
    if (row.key === "media_dir" && typeof row.value === "string") {
      mediaPath.value = row.value;
    }
    if (row.key === "library_movies_path" && typeof row.value === "string") {
      moviesPath.value = row.value;
    }
    if (row.key === "library_series_path" && typeof row.value === "string") {
      seriesPath.value = row.value;
    }
    if (row.key === "downloads_dir" && typeof row.value === "string") {
      downloadsPath.value = row.value;
    }
    if (row.key === "trash_dir" && typeof row.value === "string") {
      trashPath.value = row.value;
    }
    if (row.key === "library_scan_interval_hours" && typeof row.value === "number") {
      libraryHours.value = row.value;
    }
    if (row.key === "jellyfin_url" && typeof row.value === "string") {
      jellyfinUrl.value = row.value;
    }
    if (row.key === "jellyfin_api_key") {
      jellyfinKeySet.value = row.set;
    }
    if (row.key === "jellyfin_user_id" && typeof row.value === "string") {
      jellyfinUser.value = row.value;
    }
    if (row.key === "jellyfin_path_prefix" && typeof row.value === "string") {
      jellyfinPrefix.value = row.value;
    }
    if (row.key === "jellyfin_path_map" && typeof row.value === "string") {
      jellyfinMap.value = row.value;
    }
    if (row.key === "jellyfin_poll_interval_min" && typeof row.value === "number") {
      jellyfinPoll.value = row.value;
    }
    if (row.key === "public_base_url" && typeof row.value === "string") {
      publicBase.value = row.value;
    }
    if (row.key === "notify_group_seconds" && typeof row.value === "number") {
      groupSeconds.value = row.value;
    }
    if (row.key === "notify_quiet_start" && typeof row.value === "string") {
      quietStart.value = row.value;
    }
    if (row.key === "notify_quiet_end" && typeof row.value === "string") {
      quietEnd.value = row.value;
    }
    if (row.key === "incomplete_cleanup_enabled" && typeof row.value === "boolean") {
      incompleteCleanup.value = row.value;
    }
    if (row.key === "auto_delete_enabled" && typeof row.value === "boolean") {
      autoDeleteEnabled.value = row.value;
    }
    if (row.key === "auto_delete_days" && typeof row.value === "number") {
      autoDays.value = row.value;
    }
    if (row.key === "cleanup_notice_hours" && typeof row.value === "number") {
      noticeHours.value = row.value;
    }
    if (row.key === "trash_retention_days" && typeof row.value === "number") {
      trashDays.value = row.value;
    }
    if (row.key === "disk_min_free_gb" && typeof row.value === "number") {
      diskMinGb.value = row.value;
    }
    if (row.key === "backup_keep" && typeof row.value === "number") {
      backupKeep.value = row.value;
    }
    if (row.key === "alldebrid_auto_purge" && typeof row.value === "boolean") {
      alldebridAutoPurge.value = row.value;
    }
    if (row.key === "auto_download_default" && typeof row.value === "boolean") {
      autoDownloadDefault.value = row.value;
    }
    if (row.key === "library_auto_follow" && typeof row.value === "boolean") {
      libraryAutoFollow.value = row.value;
    }
    if (row.key === "downloads_completed_action" && typeof row.value === "string") {
      completedAction.value = row.value;
    }
    if (row.key === "jellyfin_push_watched" && typeof row.value === "boolean") {
      pushWatched.value = row.value;
    }
    if (row.key === "jellyfin_auto_rename" && typeof row.value === "boolean") {
      autoRename.value = row.value;
    }
    if (row.key === "ghcr_read_token") ghcrSet.value = row.set;
    if (row.key === "github_update_token") githubSet.value = row.set;
    if (row.key === "watchtower_url" && typeof row.value === "string") {
      watchtowerUrl.value = row.value;
      watchtowerUrlStored.value = row.value;
    }
    if (row.key === "watchtower_token") watchtowerTokenSet.value = row.set;
    if (row.key === "notify_on_update" && typeof row.value === "boolean") {
      notifyOnUpdate.value = row.value;
    }
  }
}

async function ensureBootstrap() {
  if (bootstrapDone.value) {
    return;
  }
  await loadProfiles();
  const body = await fetchSettings();
  await applySettingsBody(body);
  bootstrapDone.value = true;
}

async function loadSectionData(slug: string) {
  try {
    await ensureBootstrap();
  } catch {
    /* d’autres sections dépendent du bootstrap ; le panneau repli charge ses propres données */
  }
  if (loadedSections.value.has(slug)) {
    return;
  }
  loadedSections.value.add(slug);
  switch (slug) {
    case "qualite":
      await loadProfileFallbackPanel();
      break;
    case "scan":
      await loadStorage();
      break;
    case "jellyfin":
      await loadJellyfin();
      break;
    case "notifications":
      await loadNotifications();
      break;
    case "nettoyage":
      await loadTrash();
      await loadCleanupMigrationBanner();
      break;
    case "jetons":
      await loadTokens();
      break;
    case "maj":
      await loadUpdate();
      break;
    default:
      break;
  }
}

watch(
  section,
  (slug) => {
    if (!slug || !SETTINGS_SECTION_SLUGS.has(slug)) {
      return;
    }
    void loadSectionData(slug);
  },
  { immediate: true },
);

async function loadStorage() {
  try {
    const layout = await api<{
      media_dir: string;
      movies_dir: string;
      series_dir: string;
      downloads_dir: string;
      trash_dir: string;
      warning: string | null;
    }>("/api/v1/storage");
    storageHint.value = {
      media_dir: layout.media_dir,
      movies_dir: layout.movies_dir,
      series_dir: layout.series_dir,
      downloads_dir: layout.downloads_dir,
      trash_dir: layout.trash_dir,
      warning: layout.warning || "",
    };
  } catch {
    storageHint.value.warning = "";
  }
}

async function loadJellyfin() {
  try {
    const status = await api<{
      webhook_url: string | null;
      webhook_token_set: boolean;
      webhook_token_preview: string | null;
      webhook_token_header: string;
      last_sync_at: string | null;
      last_sync_error: string | null;
    }>("/api/v1/integrations/jellyfin");
    webhookUrl.value = status.webhook_url || "";
    webhookTokenSet.value = status.webhook_token_set;
    webhookTokenPreview.value = status.webhook_token_preview || "";
    webhookTokenHeader.value = status.webhook_token_header || "X-MediaTracker-Token";
    webhookTokenRevealed.value = "";
    jellyfinSyncAt.value = status.last_sync_at;
    jellyfinSyncError.value = status.last_sync_error;
  } catch {
    webhookUrl.value = "";
    webhookTokenSet.value = false;
    webhookTokenPreview.value = "";
    webhookTokenRevealed.value = "";
  }
  await loadJellyfinUsers();
}

async function loadJellyfinUsers() {
  try {
    const result = await api<{ users: { id: string; name: string }[] }>("/api/v1/integrations/jellyfin/users", {
      method: "POST",
      body: JSON.stringify({
        url: jellyfinUrl.value || null,
        api_key: jellyfinKey.value || null,
      }),
    });
    jellyfinUsers.value = result.users;
    const current = jellyfinUser.value.trim();
    if (!current || result.users.some((user) => user.id === current)) {
      return;
    }
    const named = result.users.find((user) => user.name.toLocaleLowerCase() === current.toLocaleLowerCase());
    if (named) {
      jellyfinUser.value = named.id;
    }
  } catch {
    jellyfinUsers.value = [];
  }
}

async function save() {
  message.value = "";
  error.value = "";
  try {
    await patchSettings({
      values: {
        ...values,
        scan_ongoing_only: ongoing.value,
        library_scan_enabled: libraryEnabled.value,
        media_dir: mediaPath.value,
        library_movies_path: moviesPath.value,
        library_series_path: seriesPath.value,
        downloads_dir: downloadsPath.value,
        trash_dir: trashPath.value,
        library_scan_interval_hours: libraryHours.value,
        auto_download_default: autoDownloadDefault.value,
        library_auto_follow: libraryAutoFollow.value,
        downloads_completed_action: completedAction.value,
      },
    });
    message.value = "Enregistré.";
    await loadStorage();
  } catch (err) {
    error.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function saveJellyfin() {
  jellyfinMessage.value = "";
  jellyfinError.value = "";
  const values: Record<string, string | number | boolean> = {
    jellyfin_url: jellyfinUrl.value,
    jellyfin_user_id: jellyfinUser.value,
    jellyfin_path_prefix: jellyfinPrefix.value,
    jellyfin_path_map: jellyfinMap.value,
    jellyfin_poll_interval_min: jellyfinPoll.value,
    jellyfin_push_watched: pushWatched.value,
    jellyfin_auto_rename: autoRename.value,
  };
  if (jellyfinKey.value) {
    values.jellyfin_api_key = jellyfinKey.value;
  }
  try {
    await patchSettings({ values });
    jellyfinKey.value = "";
    jellyfinKeySet.value = jellyfinKeySet.value || Boolean(values.jellyfin_api_key);
    jellyfinMessage.value = "Jellyfin enregistré.";
    await loadJellyfin();
  } catch (err) {
    jellyfinError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function loadNotifications() {
  const catalog = await api<{ events: NotifyEvent[] }>("/api/v1/events/notifications");
  notifyEvents.value = catalog.events;
  if (selectedEvents.value.length === 0 && editingTargetId.value == null) {
    selectedEvents.value = catalog.events.filter((row) => row.default_on !== false).map((row) => row.type);
  }
  targets.value = await api<WebhookTarget[]>("/api/v1/webhooks");
}

async function saveNotifications() {
  notifyMessage.value = "";
  notifyError.value = "";
  try {
    await patchSettings({
      values: {
        public_base_url: publicBase.value,
        notify_group_seconds: groupSeconds.value,
        notify_quiet_start: quietStart.value,
        notify_quiet_end: quietEnd.value,
      },
    });
    notifyMessage.value = "Notifications enregistrées.";
  } catch (err) {
    notifyError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

function checkedFrom(event: Event): boolean {
  return event.target instanceof HTMLInputElement && event.target.checked;
}

function toggleEvent(type: string, checked: boolean) {
  if (checked) {
    selectedEvents.value = [...new Set([...selectedEvents.value, type])];
  } else {
    selectedEvents.value = selectedEvents.value.filter((item) => item !== type);
  }
}

async function addTarget() {
  notifyMessage.value = "";
  notifyError.value = "";
  let url = genericUrl.value;
  let secret: string | undefined;
  if (targetType.value === "homeassistant") {
    const host = haHost.value.replace(/\/$/, "");
    url = `${host}/api/webhook/${haId.value || "mediatracker"}`;
  } else if (targetType.value === "ntfy") {
    url = ntfyUrl.value;
    secret = ntfyToken.value || undefined;
  } else {
    secret = genericSecret.value;
  }
  try {
    await api("/api/v1/webhooks", {
      method: "POST",
      body: JSON.stringify({
        name: targetName.value,
        type: targetType.value,
        url,
        secret: secret || null,
        events: selectedEvents.value,
        enabled: true,
      }),
    });
    notifyMessage.value = "Cible ajoutée.";
    genericSecret.value = "";
    ntfyToken.value = "";
    await loadNotifications();
  } catch (err) {
    notifyError.value = userFacingApiError(err, "Cible refusée.");
  }
}

async function testTarget(id: number) {
  notifyMessage.value = "";
  notifyError.value = "";
  try {
    const result = await api<{ ok: boolean; message: string }>(`/api/v1/webhooks/${id}/test`, { method: "POST" });
    targetNotes.value = { ...targetNotes.value, [id]: { ok: result.ok, message: result.message } };
  } catch (err) {
    const message = userFacingApiError(err, "Test impossible.");
    targetNotes.value = { ...targetNotes.value, [id]: { ok: false, message } };
  }
}

async function removeTarget(id: number) {
  notifyError.value = "";
  await api(`/api/v1/webhooks/${id}`, { method: "DELETE" });
  if (editingTargetId.value === id) {
    editingTargetId.value = null;
    selectedEvents.value = [];
  }
  await loadNotifications();
}

function editTargetEvents(target: WebhookTarget) {
  editingTargetId.value = target.id;
  selectedEvents.value = [...target.events];
  notifyMessage.value = "";
  notifyError.value = "";
}

async function saveTargetEvents() {
  if (editingTargetId.value == null) {
    return;
  }
  notifyMessage.value = "";
  notifyError.value = "";
  try {
    await api(`/api/v1/webhooks/${editingTargetId.value}`, {
      method: "PATCH",
      body: JSON.stringify({ events: selectedEvents.value }),
    });
    notifyMessage.value = "Événements de la cible enregistrés.";
    editingTargetId.value = null;
    selectedEvents.value = [];
    await loadNotifications();
  } catch (err) {
    notifyError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function loadCleanupMigrationBanner() {
  try {
    const body = await api<{ message: string | null }>("/api/v1/cleanup/migration-banner");
    cleanupMigrationBanner.value = body.message || "";
  } catch {
    cleanupMigrationBanner.value = "";
  }
}

async function dismissCleanupMigrationBanner() {
  await api("/api/v1/cleanup/migration-banner/dismiss", { method: "POST" });
  cleanupMigrationBanner.value = "";
}

async function saveCleanup() {
  cleanupMessage.value = "";
  cleanupError.value = "";
  try {
    await patchSettings({
      values: {
        incomplete_cleanup_enabled: incompleteCleanup.value,
        auto_delete_enabled: autoDeleteEnabled.value,
        auto_delete_days: autoDays.value,
        cleanup_notice_hours: noticeHours.value,
        trash_retention_days: trashDays.value,
      },
    });
    cleanupMessage.value = "Nettoyage enregistré.";
  } catch (err) {
    cleanupError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function loadPreview() {
  cleanupMessage.value = "";
  cleanupError.value = "";
  try {
    const body = await api<{ due: PreviewFile[]; upcoming: PreviewFile[] }>("/api/v1/cleanup/preview");
    previewDue.value = body.due;
    previewUpcoming.value = body.upcoming;
  } catch (err) {
    cleanupError.value = userFacingApiError(err, "Aperçu impossible.");
  }
}

async function loadTrash() {
  const body = await api<{ items: TrashItem[]; trash_bytes: number; reclaimed_bytes: number }>("/api/v1/trash");
  trashItems.value = body.items;
  trashBytes.value = body.trash_bytes;
  reclaimedBytes.value = body.reclaimed_bytes;
}

async function restoreTrash(id: number) {
  cleanupError.value = "";
  try {
    await api(`/api/v1/trash/${id}/restore`, { method: "POST" });
    cleanupMessage.value = "Fichier restauré.";
    await loadTrash();
  } catch (err) {
    cleanupError.value = userFacingApiError(err, "Restauration impossible.");
  }
}

async function purgeTrash(item: TrashItem) {
  cleanupError.value = "";
  const size = formatBytes(item.size);
  if (!window.confirm(`Supprimer définitivement « ${item.label} » (${size}) de la corbeille ?`)) {
    return;
  }
  try {
    await api(`/api/v1/trash/${item.id}`, { method: "DELETE" });
    cleanupMessage.value = `${item.label} retiré de la corbeille.`;
    await loadTrash();
  } catch (err) {
    cleanupError.value = userFacingApiError(err, "Suppression impossible.");
  }
}

async function emptyTrash() {
  cleanupError.value = "";
  const count = trashItems.value.length;
  const size = formatBytes(trashBytes.value);
  const noun = count > 1 ? "fichiers" : "fichier";
  if (
    !window.confirm(
      `Vider la corbeille supprime définitivement ${count} ${noun} (${size}). Continuer ?`,
    )
  ) {
    return;
  }
  try {
    const result = await api<{ purged: number; size_label: string }>("/api/v1/trash/empty", {
      method: "POST",
      body: JSON.stringify({ confirm: true }),
    });
    cleanupMessage.value = `Corbeille vidée : ${result.purged} fichier(s), ${result.size_label} libérés.`;
    await loadTrash();
  } catch (err) {
    cleanupError.value = userFacingApiError(err, "Vidage impossible.");
  }
}

function formatBytes(size: number): string {
  if (size >= 1024 * 1024 * 1024) {
    return `${(size / (1024 * 1024 * 1024)).toFixed(1)} Go`;
  }
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} Mo`;
  }
  if (size >= 1024) {
    return `${(size / 1024).toFixed(1)} Ko`;
  }
  return `${size} o`;
}

async function saveDebrid() {
  alldebridMessage.value = "";
  alldebridError.value = "";
  try {
    await patchSettings({ values: { alldebrid_auto_purge: alldebridAutoPurge.value } });
    alldebridMessage.value = "Réglage AllDebrid enregistré.";
  } catch (err) {
    alldebridError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function purgeDebrid(everything: boolean) {
  alldebridMessage.value = "";
  alldebridError.value = "";
  if (
    everything &&
    !window.confirm(
      "Tout purger vide l'historique récent AllDebrid et retire les aimants du compte, y compris ceux que MediaTracker n'a pas créés. Continuer ?",
    )
  ) {
    return;
  }
  try {
    const result = await api<{ message: string }>("/api/v1/integrations/alldebrid/purge", {
      method: "POST",
      body: JSON.stringify({ scope: everything ? "all" : "mediatracker", confirm: everything }),
    });
    alldebridMessage.value = result.message;
  } catch (err) {
    alldebridError.value = userFacingApiError(err, "Purge impossible.");
  }
}

async function saveDisk() {
  diskMessage.value = "";
  diskError.value = "";
  try {
    await patchSettings({
      values: { disk_min_free_gb: diskMinGb.value, backup_keep: backupKeep.value },
    });
    diskMessage.value = "Enregistré.";
  } catch (err) {
    diskError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

function toggleScope(scope: string, checked: boolean) {
  if (checked) {
    tokenScopes.value = [...new Set([...tokenScopes.value, scope])];
  } else {
    tokenScopes.value = tokenScopes.value.filter((item) => item !== scope);
  }
}

async function loadTokens() {
  try {
    const body = await api<{ items: ApiTokenRow[] }>("/api/v1/tokens");
    tokens.value = body.items;
  } catch {
    tokens.value = [];
  }
}

async function createToken() {
  tokenError.value = "";
  freshToken.value = "";
  try {
    const created = await api<ApiTokenRow & { token: string }>("/api/v1/tokens", {
      method: "POST",
      body: JSON.stringify({ name: tokenName.value, scopes: tokenScopes.value }),
    });
    freshToken.value = created.token;
    tokenName.value = "";
    await loadTokens();
  } catch (err) {
    tokenError.value = userFacingApiError(err, "Création impossible.");
  }
}

async function changeAccountPassword() {
  accountError.value = "";
  accountMessage.value = "";
  accountBusy.value = true;
  try {
    await api("/api/v1/auth/password", {
      method: "POST",
      body: JSON.stringify({
        current_password: accountCurrentPassword.value,
        new_password: accountNewPassword.value,
      }),
    });
    accountCurrentPassword.value = "";
    accountNewPassword.value = "";
    accountMessage.value = "Mot de passe mis à jour.";
  } catch (err) {
    accountError.value = userFacingApiError(err, "Échec du changement de mot de passe.");
  } finally {
    accountBusy.value = false;
  }
}

async function revokeOtherSessions() {
  if (!window.confirm("Déconnecter toutes les autres sessions sur ce compte ?")) {
    return;
  }
  accountError.value = "";
  accountMessage.value = "";
  try {
    await api("/api/v1/auth/sessions/revoke-others", { method: "POST" });
    accountMessage.value = "Les autres sessions ont été déconnectées.";
  } catch (err) {
    accountError.value = userFacingApiError(err, "Impossible de révoquer les autres sessions.");
  }
}

async function revokeToken(id: number) {
  tokenError.value = "";
  await api(`/api/v1/tokens/${id}`, { method: "DELETE" });
  if (freshToken.value) {
    freshToken.value = "";
  }
  await loadTokens();
}

async function resetDatabase() {
  resetError.value = "";
  resetMessage.value = "";
  resetting.value = true;
  try {
    const report = await api<{ backup: string; detail: string }>("/api/v1/maintenance/reset", {
      method: "POST",
      body: JSON.stringify({ confirm: resetConfirm.value.trim() }),
    });
    resetMessage.value = `${report.detail} Fichier : ${report.backup}.`;
    resetConfirm.value = "";
  } catch (err) {
    resetError.value = userFacingApiError(err, "Réinitialisation impossible.");
  } finally {
    resetting.value = false;
  }
}

const jellyfinSyncMessage = ref("");
const jellyfinSyncAt = ref<string | null>(null);
const jellyfinSyncError = ref<string | null>(null);

const webhookTokenDisplay = computed(() => webhookTokenRevealed.value || webhookTokenPreview.value || "");

async function revealWebhookToken() {
  jellyfinMessage.value = "";
  jellyfinError.value = "";
  try {
    const body = await api<{ webhook_token: string }>("/api/v1/integrations/jellyfin/webhook-token/reveal", {
      method: "POST",
    });
    webhookTokenRevealed.value = body.webhook_token;
  } catch (err) {
    jellyfinError.value = userFacingApiError(err, "Affichage impossible.");
  }
}

async function regenerateWebhookToken() {
  if (
    !window.confirm(
      "Un nouveau jeton invalidera l'ancien. Mettez à jour le plugin Jellyfin avec le nouveau jeton. Continuer ?",
    )
  ) {
    return;
  }
  jellyfinMessage.value = "";
  jellyfinError.value = "";
  try {
    const body = await api<{ webhook_token: string }>("/api/v1/integrations/jellyfin/webhook-token/regenerate", {
      method: "POST",
    });
    webhookTokenRevealed.value = body.webhook_token;
    webhookTokenSet.value = true;
    webhookTokenPreview.value = body.webhook_token.length > 4
      ? `${"•".repeat(body.webhook_token.length - 4)}${body.webhook_token.slice(-4)}`
      : body.webhook_token;
    jellyfinMessage.value = "Nouveau jeton webhook généré. Mettez à jour le plugin Jellyfin.";
  } catch (err) {
    jellyfinError.value = userFacingApiError(err, "Régénération impossible.");
  }
}

async function copyWebhookToken() {
  const text = webhookTokenRevealed.value;
  if (!text) {
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    jellyfinMessage.value = "Jeton copié.";
  } catch {
    jellyfinError.value = "Copie impossible.";
  }
}

async function syncJellyfin() {
  jellyfinMessage.value = "";
  jellyfinError.value = "";
  jellyfinSyncMessage.value = "";
  try {
    const result = await api<{ updated: number; message: string }>("/api/v1/integrations/jellyfin/sync", {
      method: "POST",
    });
    jellyfinSyncMessage.value = result.message;
    await loadJellyfin();
  } catch (err) {
    jellyfinError.value = userFacingApiError(err, "Synchronisation impossible.");
  }
}

const updateInfo = ref("");
const updateError = ref("");
const notifyOnUpdate = ref(false);
const updateNotifySaved = ref(false);
const ghcrSet = ref(false);
const githubSet = ref(false);
const ghcrToken = ref("");
const githubToken = ref("");
const watchtowerUrl = ref("");
const watchtowerUrlStored = ref("");
const watchtowerToken = ref("");
const watchtowerTokenSet = ref(false);
const updateTokensMessage = ref("");
const watchtowerConfigured = ref(false);
const updateSnapshot = ref({
  version: "",
  currentSha: "",
  buildDate: "",
  checkedAt: "",
  updateAvailable: false,
  remoteSha: "",
});
const activeDownloads = ref(0);
type UpdateNowUiState = "idle" | "loading" | "sent" | "up-to-date" | "unreachable" | "token-refused";
const updateNowState = ref<UpdateNowUiState>("idle");
const updateNowMessage = ref("");
const restartWaitSeconds = ref(0);
let restartPollTimer: ReturnType<typeof setInterval> | null = null;
let restartDeadline = 0;
let healthBaseline: { version: string; sha: string | null } | null = null;
let healthOutageSeen = false;

const watchtowerUnavailableLine = computed(() => {
  if (watchtowerConfigured.value) {
    return "";
  }
  const missingUrl = !watchtowerUrl.value.trim();
  const missingToken = !watchtowerTokenSet.value;
  if (missingUrl && missingToken) {
    return "Indisponible : renseignez l'URL et le jeton Watchtower dans Réglages → Jetons d'API, bloc « Secrets pour services distants ».";
  }
  if (missingUrl) {
    return "Indisponible : renseignez l'URL Watchtower dans Réglages → Jetons d'API, bloc « Secrets pour services distants ».";
  }
  if (missingToken) {
    return "Indisponible : renseignez le jeton Watchtower (variable WATCHTOWER_HTTP_API_TOKEN) dans le même bloc, pas un jeton Accès API.";
  }
  return "Indisponible : renseignez l'URL et le jeton Watchtower dans Réglages → Jetons d'API, bloc « Secrets pour services distants ».";
});

const updateNowButtonLabel = computed(() => {
  switch (updateNowState.value) {
    case "loading":
      return "Demande en cours…";
    case "sent":
      return "Demande envoyée";
    case "unreachable":
    case "token-refused":
      return "Réessayer";
    default:
      return "Mettre à jour maintenant";
  }
});

const updateNowButtonDisabled = computed(() => {
  if (!watchtowerConfigured.value) {
    return true;
  }
  return updateNowState.value === "loading" || updateNowState.value === "sent";
});

function formatCheckedAt(iso: string | null | undefined): string | null {
  if (!iso) {
    return null;
  }
  const parsed = parseInstant(iso);
  if (!parsed) {
    return null;
  }
  const day = parsed.toLocaleDateString("fr-FR", { dateStyle: "long" });
  const today = new Date().toLocaleDateString("fr-FR", { dateStyle: "long" });
  const clock = formatParisClock(iso);
  if (day === today) {
    return clock ? `aujourd'hui ${clock}` : "aujourd'hui";
  }
  return clock ? `${day} ${clock}` : day;
}

function stopRestartPoll() {
  if (restartPollTimer) {
    clearInterval(restartPollTimer);
    restartPollTimer = null;
  }
}

function formatRestartWait(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

async function pollHealthForRestart() {
  try {
    const body = await api<{ version: string; sha?: string | null }>("/api/v1/health");
    const sha = body.sha ?? null;
    if (!healthBaseline) {
      healthBaseline = { version: body.version, sha };
      return;
    }
    const changed = body.version !== healthBaseline.version || sha !== healthBaseline.sha;
    if (healthOutageSeen || changed) {
      window.location.reload();
      return;
    }
  } catch {
    healthOutageSeen = true;
  }
  const remaining = Math.max(0, Math.ceil((restartDeadline - Date.now()) / 1000));
  restartWaitSeconds.value = remaining;
  if (remaining <= 0) {
    stopRestartPoll();
    updateNowMessage.value = "Watchtower n'a pas relancé MediaTracker. Voir ses journaux.";
  }
}

function beginRestartWait() {
  stopRestartPoll();
  healthBaseline = null;
  healthOutageSeen = false;
  restartDeadline = Date.now() + 180_000;
  restartWaitSeconds.value = 180;
  updateNowState.value = "sent";
  void pollHealthForRestart();
  restartPollTimer = setInterval(() => {
    void pollHealthForRestart();
  }, 3000);
}

onUnmounted(() => {
  stopRestartPoll();
});

function updateNowErrorState(err: ApiError): UpdateNowUiState {
  if (err.code === "token_refused") {
    return "token-refused";
  }
  if (err.code === "unreachable") {
    return "unreachable";
  }
  return "unreachable";
}

function treatAsSent(err: unknown): boolean {
  if (isNetworkError(err)) {
    return true;
  }
  if (err instanceof ApiError) {
    if (err.code) {
      return false;
    }
    if (isTransientHttpStatus(err.status)) {
      return true;
    }
  }
  return false;
}

async function updateNow() {
  if (!watchtowerConfigured.value || updateNowState.value === "loading" || updateNowState.value === "sent") {
    return;
  }
  if (
    activeDownloads.value > 0 &&
    !window.confirm(
      `${activeDownloads.value} téléchargement(s) en cours. Watchtower peut arrêter MediaTracker sans préavis. Continuer ?`,
    )
  ) {
    return;
  }
  updateError.value = "";
  updateNowMessage.value = "";
  updateNowState.value = "loading";
  try {
    const body = await api<{ detail: string; code?: string }>("/api/v1/ha/actions/update-now", { method: "POST" });
    if (body.code === "already_up_to_date") {
      updateNowState.value = "up-to-date";
      updateNowMessage.value = body.detail;
      return;
    }
    updateNowMessage.value = body.detail;
    beginRestartWait();
  } catch (err) {
    if (err instanceof ApiError && err.code) {
      updateNowState.value = updateNowErrorState(err);
      updateNowMessage.value = err.message;
      return;
    }
    if (treatAsSent(err)) {
      updateNowMessage.value =
        "Demande envoyée à Watchtower. L'appli va redémarrer d'ici une minute environ, cette page se rechargera toute seule.";
      beginRestartWait();
      return;
    }
    const message = userFacingApiError(err, "Mise à jour impossible.");
    updateNowState.value = "unreachable";
    updateNowMessage.value = message;
  }
}

function formatBuildDate(iso: string | null | undefined): string | null {
  if (!iso) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" });
}

async function saveNotifyOnUpdate(enabled: boolean) {
  updateNotifySaved.value = false;
  updateError.value = "";
  try {
    await patchSettings({ values: { notify_on_update: enabled } });
    notifyOnUpdate.value = enabled;
    updateNotifySaved.value = true;
  } catch (err) {
    updateError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function loadUpdate() {
  try {
    const body = await api<{
      current_sha: string | null;
      build_date: string | null;
      detail: string;
      update_available: boolean;
      remote_sha: string | null;
      checked_at: string | null;
      version: string | null;
      watchtower_configured: boolean;
      active_downloads: number;
    }>("/api/v1/ha/update");
    watchtowerConfigured.value = body.watchtower_configured;
    activeDownloads.value = body.active_downloads ?? 0;
    updateSnapshot.value = {
      version: body.version ?? "",
      currentSha: body.current_sha ? body.current_sha.slice(0, 12) : "inconnue",
      buildDate: formatBuildDate(body.build_date) ?? "",
      checkedAt: formatCheckedAt(body.checked_at) ?? "",
      updateAvailable: body.update_available,
      remoteSha: body.remote_sha ? body.remote_sha.slice(0, 12) : "",
    };
    updateInfo.value = body.detail;
  } catch (err) {
    updateError.value = userFacingApiError(err, "Version indisponible.");
  }
}

async function saveUpdateTokens() {
  updateError.value = "";
  updateTokensMessage.value = "";
  const values: Record<string, string> = {};
  if (ghcrToken.value) values.ghcr_read_token = ghcrToken.value;
  if (githubToken.value) values.github_update_token = githubToken.value;
  if (watchtowerUrl.value.trim()) values.watchtower_url = watchtowerUrl.value.trim();
  if (watchtowerToken.value) values.watchtower_token = watchtowerToken.value;
  if (!Object.keys(values).length) return;
  try {
    await patchSettings({ values });
    ghcrToken.value = "";
    githubToken.value = "";
    watchtowerToken.value = "";
    ghcrSet.value = ghcrSet.value || "ghcr_read_token" in values;
    githubSet.value = githubSet.value || "github_update_token" in values;
    watchtowerTokenSet.value = watchtowerTokenSet.value || "watchtower_token" in values;
    if ("watchtower_url" in values) {
      watchtowerUrlStored.value = watchtowerUrl.value.trim();
    }
    await loadUpdate();
    updateTokensMessage.value = "Enregistré.";
  } catch (err) {
    updateError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function checkUpdate() {
  updateError.value = "";
  try {
    const body = await api<{ detail: string; update_available: boolean }>("/api/v1/ha/actions/check-update", {
      method: "POST",
    });
    updateInfo.value = body.detail;
  } catch (err) {
    updateError.value = userFacingApiError(err, "Vérification impossible.");
  }
}

async function restartApp() {
  if (
    !window.confirm(
      "Redémarrer MediaTracker ? Les téléchargements en cours sont mis en pause, puis le conteneur s'arrête pour que Docker le relance.",
    )
  ) {
    return;
  }
  updateError.value = "";
  try {
    const body = await api<{ detail: string }>("/api/v1/ha/actions/restart", { method: "POST" });
    updateInfo.value = body.detail;
  } catch (err) {
    updateInfo.value = "Redémarrage demandé.";
    updateError.value = userFacingApiError(err, "");
  }
}

async function testJellyfin() {
  jellyfinMessage.value = "";
  jellyfinError.value = "";
  try {
    const result = await api<{ ok: boolean; message: string; user_id?: string | null }>("/api/v1/integrations/jellyfin/test", {
      method: "POST",
      body: JSON.stringify({
        url: jellyfinUrl.value || null,
        api_key: jellyfinKey.value || null,
        user_id: jellyfinUser.value,
      }),
    });
    if (result.ok) {
      jellyfinMessage.value = result.message;
      if (result.user_id) {
        jellyfinUser.value = result.user_id;
      }
      await loadJellyfinUsers();
    } else {
      jellyfinError.value = result.message;
    }
  } catch (err) {
    jellyfinError.value = userFacingApiError(err, "Connexion impossible.");
  }
}

async function loadProfiles() {
  const body = await api<{ profiles: QualityProfileRow[] }>("/api/v1/quality-profiles");
  profiles.value = body.profiles;
}

function resetProfile() {
  profileId.value = null;
  profileName.value = "";
  profileLanguages.value = "VFF, VFQ, VF, MULTI, VOSTFR";
  profileMinResolution.value = "";
  profileMaxResolution.value = "1080p";
  profileMinSize.value = "";
  profileMaxSize.value = "";
  profileKeywords.value = "";
  profileError.value = "";
  profileMessage.value = "";
}

async function editProfile(profile: QualityProfileRow) {
  profileId.value = profile.id;
  profileName.value = profile.name;
  profileLanguages.value = profile.languages.join(", ");
  profileMinResolution.value = profile.min_resolution || "";
  profileMaxResolution.value = profile.max_resolution || "";
  profileMinSize.value = profile.min_size_mb == null ? "" : String(profile.min_size_mb);
  profileMaxSize.value = profile.max_size_mb == null ? "" : String(profile.max_size_mb);
  profileKeywords.value = profile.exclude_keywords.join(", ");
  profileError.value = "";
  profileMessage.value = "";
  await nextTick();
  profileFormRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function profilePayload() {
  const size = (value: string) => {
    const text = value.trim();
    return text === "" ? null : Number(text);
  };
  return {
    name: profileName.value.trim(),
    languages: profileLanguages.value.split(",").map((item) => item.trim()).filter(Boolean),
    min_resolution: profileMinResolution.value || null,
    max_resolution: profileMaxResolution.value || null,
    min_size_mb: size(profileMinSize.value),
    max_size_mb: size(profileMaxSize.value),
    exclude_keywords: profileKeywords.value.split(",").map((item) => item.trim()).filter(Boolean),
  };
}

async function saveProfile() {
  profileError.value = "";
  profileMessage.value = "";
  try {
    const creating = profileId.value == null;
    const path = creating ? "/api/v1/quality-profiles" : `/api/v1/quality-profiles/${profileId.value}`;
    await api(path, {
      method: creating ? "POST" : "PATCH",
      body: JSON.stringify(profilePayload()),
    });
    resetProfile();
    profileMessage.value = creating ? "Profil ajouté." : "Profil enregistré.";
    await loadProfiles();
  } catch (err) {
    profileError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function makeDefault(profile: QualityProfileRow) {
  profileError.value = "";
  profileMessage.value = "";
  try {
    await api(`/api/v1/quality-profiles/${profile.id}`, {
      method: "PATCH",
      body: JSON.stringify({ is_default: true }),
    });
    profileMessage.value = `« ${profile.name} » est le profil par défaut.`;
    await loadProfiles();
  } catch (err) {
    profileError.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function removeProfile(profile: QualityProfileRow) {
  if (!window.confirm(`Supprimer le profil « ${profile.name} » ?`)) {
    return;
  }
  profileError.value = "";
  profileMessage.value = "";
  try {
    await api(`/api/v1/quality-profiles/${profile.id}`, { method: "DELETE" });
    if (profileId.value === profile.id) {
      resetProfile();
    }
    profileMessage.value = "Profil supprimé.";
    await loadProfiles();
  } catch (err) {
    profileError.value = userFacingApiError(err, "Suppression impossible.");
  }
}

const fallbackOrderedProfiles = computed((): QualityProfileRow[] => {
  const byId = new Map(profiles.value.map((row) => [row.id, row]));
  return fallbackOrderIds.value.map((id) => {
    const row = byId.get(id);
    if (row) {
      return row;
    }
    return {
      id,
      name: `Profil ${id}`,
      languages: [],
      min_resolution: null,
      max_resolution: null,
      min_size_mb: null,
      max_size_mb: null,
      exclude_keywords: [],
      is_default: false,
    };
  });
});

const fallbackAddCandidates = computed(() =>
  profiles.value.filter((row) => !fallbackOrderIds.value.includes(row.id)),
);

function profileSummary(row: QualityProfileRow): string {
  return qualityProfileSummary(row);
}

function moveFallback(profileId: number, delta: -1 | 1) {
  const index = fallbackOrderIds.value.indexOf(profileId);
  if (index < 0) {
    return;
  }
  const target = index + delta;
  if (target < 0 || target >= fallbackOrderIds.value.length) {
    return;
  }
  const next = [...fallbackOrderIds.value];
  const [item] = next.splice(index, 1);
  next.splice(target, 0, item);
  fallbackOrderIds.value = next;
}

function removeFallbackId(profileId: number) {
  fallbackOrderIds.value = fallbackOrderIds.value.filter((row) => row !== profileId);
}

function addFallbackProfile() {
  fallbackError.value = "";
  const parsed = Number(fallbackAddId.value);
  if (!Number.isInteger(parsed) || parsed <= 0) {
    fallbackError.value = "Choisissez un profil à ajouter.";
    return;
  }
  if (fallbackOrderIds.value.includes(parsed)) {
    fallbackError.value = "Ce profil est déjà dans la liste.";
    return;
  }
  fallbackOrderIds.value = [...fallbackOrderIds.value, parsed];
  fallbackAddId.value = "";
}

function applyFallbackPanelFromSettings(body: Awaited<ReturnType<typeof fetchSettings>>) {
  for (const row of body.settings) {
    if (row.key === "allow_fallback" && typeof row.value === "boolean") {
      allowFallback.value = row.value;
    }
    if (row.key === PROFILE_FALLBACK_ORDER_KEY) {
      fallbackOrderIds.value = parseProfileFallbackOrder(
        typeof row.value === "string" ? row.value : "",
      );
    }
  }
}

async function loadProfileFallbackPanel() {
  fallbackPanelStatus.value = "loading";
  fallbackPanelLoadError.value = "";
  fallbackError.value = "";
  try {
    await loadProfiles();
    const body = await fetchSettings();
    applyFallbackPanelFromSettings(body);
    fallbackPanelStatus.value = "ready";
  } catch (err) {
    fallbackPanelStatus.value = "error";
    fallbackPanelLoadError.value = userFacingApiError(err, "Chargement impossible.");
  }
}

async function saveProfileFallback() {
  if (fallbackPanelStatus.value !== "ready") {
    fallbackError.value = "Chargement incomplet : enregistrement impossible.";
    return;
  }
  fallbackError.value = "";
  fallbackMessage.value = "";
  fallbackSaving.value = true;
  try {
    await patchSettings({
      values: {
        allow_fallback: allowFallback.value,
        [PROFILE_FALLBACK_ORDER_KEY]: formatProfileFallbackOrder(fallbackOrderIds.value),
      },
    });
    fallbackMessage.value = "Enregistré.";
  } catch (err) {
    fallbackError.value = userFacingApiError(err, "Enregistrement impossible.");
  } finally {
    fallbackSaving.value = false;
  }
}
</script>

<template>
  <div class="settings-section-host zone">
    <section v-if="section === 'compte'" class="settings-section panel stack">
      <h2 class="settings-section-title">Compte</h2>
      <form @submit.prevent="changeAccountPassword">
        <label>
          <span>Mot de passe actuel</span>
          <input
            v-model="accountCurrentPassword"
            type="password"
            autocomplete="current-password"
            required
          />
        </label>
        <PasswordFieldWithGenerator v-model="accountNewPassword" label="Nouveau mot de passe" />
        <span class="hint">
          Au moins 12 caractères, deux types (lettres, chiffres, symboles), sans suite évidente ni nom
          d'utilisateur dedans.
        </span>
        <p v-if="accountError" class="error">{{ accountError }}</p>
        <p v-if="accountMessage">{{ accountMessage }}</p>
        <button type="submit" :disabled="accountBusy">Changer le mot de passe</button>
      </form>
      <h3 class="settings-subsection-title">Sessions</h3>
      <p class="hint">
        Ferme les connexions ouvertes sur d'autres navigateurs ou appareils. Cette session reste active.
      </p>
      <p class="actions">
        <button type="button" data-testid="revoke-other-sessions" @click="revokeOtherSessions">
          Déconnecter toutes les autres sessions
        </button>
      </p>
    </section>
    <section v-if="section === 'qualite'" class="settings-section panel stack profile-fallback-settings">
      <h2 class="settings-section-title">Profils de repli</h2>
      <p v-if="fallbackPanelStatus === 'loading'" class="hint" role="status">Chargement des réglages…</p>
      <p v-else-if="fallbackPanelStatus === 'error'" class="error" role="alert">{{ fallbackPanelLoadError }}</p>
      <p v-if="fallbackPanelStatus === 'error'" class="actions">
        <button type="button" @click="loadProfileFallbackPanel">Réessayer</button>
      </p>
      <form
        v-if="fallbackPanelStatus === 'ready'"
        class="profile-fallback-panel panel"
        @submit.prevent="saveProfileFallback"
      >
        <div class="profile-fallback-switchline">
          <h3 class="settings-subsection-title">Repli par défaut</h3>
          <div
            class="segment-toggle"
            role="group"
            aria-label="Repli par défaut pour les fiches qui suivent le global"
          >
            <button
              type="button"
              :aria-pressed="allowFallback"
              @click="allowFallback = true"
            >
              Oui
            </button>
            <button
              type="button"
              :aria-pressed="!allowFallback"
              @click="allowFallback = false"
            >
              Non
            </button>
          </div>
        </div>
        <p class="hint">
          S'applique à toutes les fiches qui suivent le réglage global (pas seulement aux nouvelles).
        </p>
        <div class="profile-fallback-section">
          <h3 class="settings-subsection-title">Ordre global des profils</h3>
          <p class="hint">
            Après le profil principal, essayer ces profils dans l'ordre choisi pour les fiches qui suivent le global,
            si « Repli par défaut » est à Oui.
          </p>
          <div
            v-for="(profile, index) in fallbackOrderedProfiles"
            :key="profile.id"
            class="profile-fallback-row"
          >
            <span class="profile-fallback-rank">{{ index + 1 }}</span>
            <div class="profile-fallback-profile">
              <strong>{{ profile.name }}</strong>
              <small class="hint">{{ profileSummary(profile) }}</small>
            </div>
            <div class="profile-fallback-actions">
              <button
                type="button"
                :disabled="index === 0"
                :aria-label="`Monter ${profile.name}`"
                @click="moveFallback(profile.id, -1)"
              >
                ↑
              </button>
              <button
                type="button"
                :disabled="index === fallbackOrderedProfiles.length - 1"
                :aria-label="`Descendre ${profile.name}`"
                @click="moveFallback(profile.id, 1)"
              >
                ↓
              </button>
              <button type="button" @click="removeFallbackId(profile.id)">Retirer</button>
            </div>
          </div>
          <div class="profile-fallback-add">
            <select v-model="fallbackAddId" aria-label="Profil existant à ajouter">
              <option value="">Choisir un profil existant…</option>
              <option v-for="profile in fallbackAddCandidates" :key="profile.id" :value="String(profile.id)">
                {{ profile.name }} — {{ profileSummary(profile) }}
              </option>
            </select>
            <button type="button" @click="addFallbackProfile">Ajouter</button>
          </div>
          <a class="profile-fallback-manage" href="#profils-qualite">Gérer les profils existants</a>
          <p class="hint">
            Aucun ajout automatique. Liste vide : aucun repli. Un profil déjà principal n'est pas essayé deux fois.
          </p>
        </div>
        <p class="profile-fallback-notice" role="note">
          Même règle pour Wawacity, Comet et Hive, y compris « Vérifier maintenant ». L'ordre de vos sources ne change pas.
        </p>
        <div class="profile-fallback-foot">
          <p class="hint">La liste est commune à toutes les fiches.</p>
          <button type="submit" class="primary" :disabled="fallbackSaving">
            {{ saveSettingsLabel() }}
          </button>
        </div>
        <p v-if="fallbackError" class="error">{{ fallbackError }}</p>
        <p v-if="fallbackMessage" class="hint">{{ fallbackMessage }}</p>
      </form>

      <h2 id="profils-qualite" class="settings-section-title">Profils de qualité</h2>
      <p class="hint">
            Langues dans l'ordre de préférence, résolution, taille et mots exclus. Wawacity et Comet utilisent le même profil.
          </p>
          <p v-for="profile in profiles" :key="profile.id">
            <strong>{{ profile.name }}</strong>
            <template v-if="profile.is_default"> · défaut</template>
            <span class="hint">
              {{ profile.languages.join(", ") }}
              · max {{ profile.max_resolution || "aucune" }}
            </span>
            <button type="button" @click="editProfile(profile)">Modifier</button>
            <button v-if="!profile.is_default" type="button" @click="makeDefault(profile)">Définir par défaut</button>
            <button v-if="!profile.is_default" type="button" @click="removeProfile(profile)">Supprimer</button>
          </p>
          <form ref="profileFormRef" @submit.prevent="saveProfile">
            <h3 class="settings-section-title">{{ profileFormHeading }}</h3>
            <label>
              <span>Nom</span>
              <input v-model="profileName" required maxlength="80" placeholder="VF/Multi 1080p" />
            </label>
            <label>
              <span>Langues, de la plus souhaitée à la moins</span>
              <input v-model="profileLanguages" required placeholder="VFF, VFQ, VF, MULTI, VOSTFR" />
              <span class="hint">VFF, VFQ, VF, TRUEFRENCH, MULTI, VOSTFR, VO. TRUEFRENCH est enregistré comme VFF.</span>
            </label>
            <div class="row">
              <label>
                <span>Résolution minimale</span>
                <select v-model="profileMinResolution">
                  <option v-for="option in resolutions" :key="'min-' + option" :value="option">{{ option || "Aucune" }}</option>
                </select>
              </label>
              <label>
                <span>Résolution maximale</span>
                <select v-model="profileMaxResolution">
                  <option v-for="option in resolutions" :key="'max-' + option" :value="option">{{ option || "Aucune" }}</option>
                </select>
              </label>
            </div>
            <div class="row">
              <label>
                <span>Taille minimale (Mo)</span>
                <input v-model="profileMinSize" inputmode="numeric" placeholder="Vide : pas de minimum" />
              </label>
              <label>
                <span>Taille maximale (Mo)</span>
                <input v-model="profileMaxSize" inputmode="numeric" placeholder="Vide : pas de maximum" />
              </label>
            </div>
            <label>
              <span>Mots exclus</span>
              <input v-model="profileKeywords" placeholder="HDR, CAM" />
              <span class="hint">Séparés par des virgules. Un fichier dont le nom contient un de ces mots est ignoré.</span>
            </label>
            <button type="submit">{{ profileId == null ? "Ajouter" : saveSettingsLabel() }}</button>
            <button v-if="profileId != null" type="button" @click="resetProfile">Nouveau</button>
          </form>
          <p v-if="profileError" class="error">{{ profileError }}</p>
          <p v-if="profileMessage" class="hint">{{ profileMessage }}</p>
    </section>
    <section v-if="section === 'scan'" class="settings-section panel stack">
      <h2 class="settings-section-title">Réglages de scan</h2>
      <form @submit.prevent="save">
            <label v-for="field in fields" :key="field.key">
              <span>{{ field.label }}</span>
              <input v-model.number="values[field.key]" type="number" required />
              <span class="hint">{{ field.hint }}</span>
            </label>
            <p class="hint">
              L’ordre des sources, le rythme commun et les URL se règlent dans
              <RouterLink to="/reglages/sources">Sources</RouterLink>.
            </p>
            <label>
              <span>Séries en cours seulement</span>
              <select v-model="ongoing">
                <option :value="true">Oui</option>
                <option :value="false">Non</option>
              </select>
              <span class="hint">Les séries terminées ou annulées ne sont pas repollées.</span>
            </label>
            <label>
              <span>Dossier médias</span>
              <input v-model="mediaPath" placeholder="/data/media" />
              <span class="hint">En vigueur : <code>{{ storageHint.media_dir }}</code>. Vide : base des films et des séries.</span>
            </label>
            <label>
              <span>Dossier des films</span>
              <input v-model="moviesPath" placeholder="/data/media/Films" />
              <span class="hint">En vigueur : <code>{{ storageHint.movies_dir }}</code>. Vide pour revenir au défaut. Accents acceptés.</span>
            </label>
            <label>
              <span>Dossier des séries</span>
              <input v-model="seriesPath" placeholder="/data/media/Series" />
              <span class="hint">En vigueur : <code>{{ storageHint.series_dir }}</code>. Vide pour revenir au défaut. Accents acceptés.</span>
            </label>
            <label>
              <span>Téléchargements</span>
              <input v-model="downloadsPath" placeholder="/data/downloads" />
              <span class="hint">En vigueur : <code>{{ storageHint.downloads_dir }}</code>. Même disque que les bibliothèques.</span>
            </label>
            <label>
              <span>Corbeille</span>
              <input v-model="trashPath" placeholder="/data/.trash" />
              <span class="hint">En vigueur : <code>{{ storageHint.trash_dir }}</code>. Même disque que les bibliothèques.</span>
            </label>
            <p v-if="storageHint.warning" class="error">{{ storageHint.warning }}</p>
            <label>
              <span>Repasse de la bibliothèque (heures)</span>
              <input v-model.number="libraryHours" type="number" min="1" max="168" required />
              <span class="hint">Défaut 24 h. Une repasse a aussi lieu après chaque fichier déposé.</span>
            </label>
            <label>
              <span>Repasse automatique</span>
              <select v-model="libraryEnabled">
                <option :value="true">Oui</option>
                <option :value="false">Non</option>
              </select>
            </label>
            <label>
              <span>Suivre automatiquement après identification</span>
              <select v-model="libraryAutoFollow">
                <option :value="false">Non</option>
                <option :value="true">Oui</option>
              </select>
              <span class="hint">Par défaut, identifier un dossier le relie à TMDB sans le suivre.</span>
            </label>
            <label>
              <span>Après téléchargement</span>
              <select v-model="completedAction">
                <option value="archive">Archiver</option>
                <option value="remove">Supprimer de la liste</option>
              </select>
              <span class="hint">Le fichier dans la bibliothèque n'est jamais supprimé.</span>
            </label>
            <label class="check">
              <input v-model="autoDownloadDefault" type="checkbox" />
              <span>Téléchargement automatique pour les nouveaux suivis</span>
            </label>
            <p class="hint">Activé par défaut. Chaque fiche peut le couper ensuite.</p>
            <p v-if="error" class="error">{{ error }}</p>
            <p v-if="message">{{ message }}</p>
            <button type="submit">{{ saveSettingsLabel() }}</button>
          </form>
    </section>
    <section v-if="section === 'jellyfin'" class="settings-section panel stack">
      <h2 class="settings-section-title">Jellyfin</h2>
      <p class="hint">
            URL et clé API du serveur sur le LAN. Après chaque fichier déposé, la bibliothèque est rafraîchie
            (une fois, après une minute). L'état « vu » suit l'utilisateur unique : webhook si l'URL est collée
            dans le plugin, sinon un passage toutes les 30 minutes.
          </p>
          <form @submit.prevent="saveJellyfin">
            <label>
              <span>URL Jellyfin</span>
              <input v-model="jellyfinUrl" placeholder="http://jellyfin:8096" />
              <span class="hint">
                Dans le même réseau Docker, <code>http://jellyfin:8096</code>. Clé API : tableau de bord Jellyfin,
                icône engrenage, puis Clés API.
              </span>
            </label>
            <label>
              <span>Clé API</span>
              <input v-model="jellyfinKey" type="password" autocomplete="off" :placeholder="jellyfinKeySet ? 'inchangée' : ''" />
            </label>
            <label>
              <span>Utilisateur Jellyfin</span>
              <select v-model="jellyfinUser">
                <option value="">Choisir un compte</option>
                <option v-if="jellyfinUser && !jellyfinUsers.some((user) => user.id === jellyfinUser)" :value="jellyfinUser">
                  {{ jellyfinUser }}
                </option>
                <option v-for="user in jellyfinUsers" :key="user.id" :value="user.id">{{ user.name }}</option>
              </select>
              <span class="hint">
                Liste issue de Jellyfin. Un nom saisi à la place de l'identifiant est converti automatiquement.
              </span>
            </label>
            <label>
              <span>Correspondance des chemins</span>
              <input
                v-model="jellyfinMap"
                placeholder="/data/movies=/data/Films;/data/tvshows=/data/Séries"
              />
              <span class="hint">
                Préfixe vu par Jellyfin, signe égal, préfixe MediaTracker. Plusieurs paires séparées par
                <code>;</code>. Exemple linuxserver : <code>/data/movies=/data/Films;/data/tvshows=/data/Séries</code>.
              </span>
            </label>
            <label>
              <span>Ancien préfixe (repli)</span>
              <input v-model="jellyfinPrefix" placeholder="/srv/media" />
              <span class="hint">Encore lu si la correspondance ne couvre pas le chemin. Sinon laissez vide.</span>
            </label>
            <label class="check">
              <input v-model="autoRename" type="checkbox" />
              <span>Renommage automatique</span>
            </label>
            <p class="hint">
              Désactivé par défaut. Quand il est activé, les dossiers déjà identifiés par Jellyfin sont renommés
              sans validation. L'aperçu reste disponible. Après un renommage, Jellyfin est rafraîchi et un « vu »
              perdu est restauré.
            </p>
            <label>
              <span>Pousser un vu manuel vers Jellyfin</span>
              <select v-model="pushWatched">
                <option :value="false">Non</option>
                <option :value="true">Oui</option>
              </select>
              <span class="hint">Désactivé par défaut. Un vu manuel n'est jamais effacé par la synchro.</span>
            </label>
            <label>
              <span>Synchro « vu » (minutes)</span>
              <input v-model.number="jellyfinPoll" type="number" min="5" max="1440" required />
              <span class="hint">
                Défaut 30. Le webhook, s'il est branché, met à jour un « vu » tout de suite.
                ItemAdded et ItemUpdated ne font que demander une lecture incrémentale : le corps du message n'est pas une source.
              </span>
            </label>
            <p v-if="webhookUrl" class="hint">
              URL du webhook (PlaybackStop, UserDataSaved, ItemAdded, ItemUpdated) :
              <code>{{ webhookUrl }}</code>.
              Configurez le jeton dans l'en-tête <code>{{ webhookTokenHeader }}</code> (prioritaire).
              Le paramètre <code>?token=</code> reste accepté pour une config existante ; Santé signale son usage.
            </p>
            <label v-if="webhookUrl && webhookTokenSet">
              <span>Jeton webhook (en-tête {{ webhookTokenHeader }})</span>
              <div class="masked-secret">
                <input :value="webhookTokenDisplay" readonly autocomplete="off" />
                <p class="actions">
                  <button type="button" @click="revealWebhookToken">Afficher</button>
                  <button type="button" @click="regenerateWebhookToken">Régénérer…</button>
                  <button v-if="webhookTokenRevealed" type="button" @click="copyWebhookToken">Copier</button>
                </p>
              </div>
            </label>
            <p class="hint">
              Si Jellyfin monte les films et les séries ailleurs, la correspondance ci-dessus aligne les chemins
              sans changer les bibliothèques Jellyfin. Santé signale des dossiers qui ne sont pas sur le même disque.
            </p>
            <p v-if="jellyfinError" class="error">{{ jellyfinError }}</p>
            <p v-if="jellyfinMessage">{{ jellyfinMessage }}</p>
            <button type="submit">{{ saveSettingsLabel() }}</button>
            <button type="button" @click="testJellyfin">Tester la connexion</button>
            <button type="button" @click="syncJellyfin">Synchroniser les vus</button>
            <p v-if="jellyfinSyncMessage" class="hint">{{ jellyfinSyncMessage }}</p>
            <p v-if="jellyfinSyncAt" class="hint">Dernière synchro : {{ jellyfinSyncAt }}</p>
            <p v-if="jellyfinSyncError" class="error">{{ jellyfinSyncError }}</p>
          </form>
    </section>
    <section v-if="section === 'notifications'" class="settings-section panel stack">
      <h2 class="settings-section-title">Notifications</h2>
      <p class="hint">
            Plusieurs cibles en même temps. Home Assistant est le choix par défaut (webhook, sans HMAC).
            ntfy et un webhook signé HMAC-SHA256 sont optionnels. Les secrets et les liens débrideur ne partent pas.
          </p>
          <form @submit.prevent="saveNotifications">
            <label>
              <span>URL de MediaTracker sur le LAN</span>
              <input v-model="publicBase" placeholder="http://192.168.1.20:8080" />
              <span class="hint">Sert de lien dans la notification, vers la fiche.</span>
            </label>
            <label>
              <span>Regroupement (secondes)</span>
              <input v-model.number="groupSeconds" type="number" min="0" max="3600" required />
              <span class="hint">Plusieurs épisodes terminés dans cette fenêtre ne font qu'une notification. 0 désactive.</span>
            </label>
            <div class="row">
              <label>
                <span>Heures calmes, début</span>
                <input v-model="quietStart" placeholder="22:00" />
              </label>
              <label>
                <span>Heures calmes, fin</span>
                <input v-model="quietEnd" placeholder="08:00" />
              </label>
            </div>
            <span class="hint">Vides : pas de pause. Fuseau Europe/Paris.</span>
            <p v-if="notifyError" class="error">{{ notifyError }}</p>
            <p v-if="notifyMessage">{{ notifyMessage }}</p>
            <button type="submit">{{ saveSettingsLabel() }}</button>
          </form>
          <form @submit.prevent="addTarget">
            <h2>Nouvelle cible</h2>
            <label>
              <span>Nom</span>
              <input v-model="targetName" required />
            </label>
            <label>
              <span>Type</span>
              <select v-model="targetType">
                <option value="homeassistant">Home Assistant</option>
                <option value="ntfy">ntfy</option>
                <option value="generic">Webhook signé</option>
              </select>
            </label>
            <template v-if="targetType === 'homeassistant'">
              <label>
                <span>URL du serveur</span>
                <input v-model="haHost" placeholder="http://homeassistant.local:8123" />
              </label>
              <label>
                <span>Identifiant du webhook</span>
                <input v-model="haId" placeholder="mediatracker" />
                <span class="hint">Défaut mediatracker. C'est le secret : il n'est pas renvoyé dans le corps.</span>
              </label>
            </template>
            <template v-else-if="targetType === 'ntfy'">
              <label>
                <span>URL du topic</span>
                <input v-model="ntfyUrl" placeholder="https://ntfy.example/mediatracker" />
              </label>
              <label>
                <span>Jeton (optionnel)</span>
                <input v-model="ntfyToken" type="password" autocomplete="off" />
              </label>
            </template>
            <template v-else>
              <label>
                <span>URL</span>
                <input v-model="genericUrl" placeholder="https://exemple/hook" />
              </label>
              <label>
                <span>Secret HMAC</span>
                <input v-model="genericSecret" type="password" autocomplete="off" />
                <span class="hint">En-têtes X-MediaTracker-Timestamp et X-MediaTracker-Signature (sha256).</span>
              </label>
            </template>
            <fieldset v-if="editingTargetId == null" class="checks">
              <legend>Événements</legend>
              <p class="hint">Nouveautés est décoché : un seul digest par actualisation, seulement si vous le cochez.</p>
              <label v-for="event in notifyEvents" :key="event.type" class="check">
                <input
                  type="checkbox"
                  :checked="selectedEvents.includes(event.type)"
                  @change="toggleEvent(event.type, checkedFrom($event))"
                />
                <span>{{ event.label }}</span>
              </label>
            </fieldset>
            <button v-if="editingTargetId == null" type="submit">Ajouter la cible</button>
          </form>
          <article v-for="target in targets" :key="target.id" class="row">
            <div>
              <strong>{{ target.name }}</strong>
              <p class="hint">{{ target.type }} · {{ target.events.length }} événement(s) · échecs {{ target.failure_count }}</p>
            </div>
            <div>
              <button type="button" @click="editTargetEvents(target)">Événements</button>
              <button type="button" @click="testTarget(target.id)">Tester</button>
              <button type="button" @click="removeTarget(target.id)">Retirer</button>
              <p v-if="targetNotes[target.id]" :class="targetNotes[target.id].ok ? 'hint' : 'error'">
                {{ targetNotes[target.id].message }}
              </p>
            </div>
          </article>
          <form v-if="editingTargetId != null" class="stack panel" @submit.prevent="saveTargetEvents">
            <p class="hint">Modifier les événements de la cible sélectionnée.</p>
            <fieldset class="checks">
              <legend>Événements</legend>
              <label v-for="event in notifyEvents" :key="`edit-${event.type}`" class="check">
                <input
                  type="checkbox"
                  :checked="selectedEvents.includes(event.type)"
                  @change="toggleEvent(event.type, checkedFrom($event))"
                />
                <span>{{ event.label }}</span>
              </label>
            </fieldset>
            <p class="actions">
              <button type="submit">Enregistrer les événements</button>
              <button type="button" @click="editingTargetId = null; selectedEvents = []">Annuler</button>
            </p>
          </form>
    </section>
    <section v-if="section === 'nettoyage'" class="settings-section panel stack">
      <h2 class="settings-section-title">Nettoyage</h2>
      <p v-if="cleanupMigrationBanner" class="hint panel migration-banner">
            {{ cleanupMigrationBanner }}
            <button type="button" @click="dismissCleanupMigrationBanner">Compris</button>
          </p>
          <p class="hint">
            Le contenu vu part à la corbeille automatiquement après le délai et le préavis ci-dessous. Le suivi
            n'est pas requis. Une saison n'est retirée que si elle est entièrement vue. Marquez « Garder » sur
            une fiche, une saison ou un épisode pour l'exclure. Les fichiers vont à la corbeille, sur le même volume.
          </p>
          <form @submit.prevent="saveCleanup">
            <label>
              <span>Incomplets automatiques</span>
              <select v-model="incompleteCleanup">
                <option :value="false">Non</option>
                <option :value="true">Oui</option>
              </select>
              <span class="hint">
                Après l'aperçu dans Bibliothèque. Les orphelins déjà en bibliothèque, les fichiers minuscules
                et les contrôles aria2 partent à la corbeille. Les autres incomplets restent trois jours.
              </span>
            </label>
            <label class="check">
              <input v-model="autoDeleteEnabled" type="checkbox" />
              <span>Suppression automatique</span>
            </label>
            <label :class="{ 'cleanup-days-muted': !autoDeleteEnabled }">
              <span>Jours après le visionnage</span>
              <input
                v-model.number="autoDays"
                type="number"
                min="1"
                max="365"
                :disabled="!autoDeleteEnabled"
                :required="autoDeleteEnabled"
              />
              <span v-if="autoDeleteEnabled" class="hint">Défaut 14. La fiche peut imposer un autre délai.</span>
              <span v-else class="hint">Désactivée : aucun fichier ne partira à la corbeille automatiquement.</span>
            </label>
            <label>
              <span>Prévenir avant suppression (heures)</span>
              <input v-model.number="noticeHours" type="number" min="1" max="168" required />
              <span class="hint">Défaut 24. Une notification part avant le déplacement.</span>
            </label>
            <label>
              <span>Corbeille (jours)</span>
              <input v-model.number="trashDays" type="number" min="1" max="90" required />
            </label>
            <p v-if="cleanupError" class="error">{{ cleanupError }}</p>
            <p v-if="cleanupMessage">{{ cleanupMessage }}</p>
            <button type="submit">{{ saveSettingsLabel() }}</button>
            <button type="button" @click="loadPreview">Aperçu du prochain passage</button>
          </form>
          <p v-if="previewDue.length === 0 && previewUpcoming.length === 0" class="hint">
            L'aperçu liste ce qui partira au prochain passage, et ce qui est seulement annoncé.
          </p>
          <div v-if="previewDue.length">
            <h2>Sera supprimé au prochain passage</h2>
            <ul>
              <li v-for="row in previewDue" :key="`${row.media_id}-${row.season}-${row.episode}`">
                {{ row.title }}
                <template v-if="row.season != null"> S{{ row.season }}</template>
                <template v-if="row.episode != null">E{{ row.episode }}</template>
                · {{ formatBytes(row.bytes) }}
              </li>
            </ul>
          </div>
          <div v-if="previewUpcoming.length">
            <h2>Annoncé, pas encore supprimé</h2>
            <ul>
              <li v-for="row in previewUpcoming" :key="`up-${row.media_id}-${row.season}-${row.episode}`">
                {{ row.title }}
                <template v-if="row.season != null"> S{{ row.season }}</template>
                <template v-if="row.episode != null">E{{ row.episode }}</template>
                · {{ formatBytes(row.bytes) }}
              </li>
            </ul>
          </div>
          <h2>Corbeille</h2>
          <p class="hint" data-testid="trash-summary">
            En corbeille : {{ trashItems.length }} fichier(s), {{ formatBytes(trashBytes) }}.
            Espace repris après purge : {{ formatBytes(reclaimedBytes) }}.
          </p>
          <p class="actions">
            <button
              type="button"
              class="danger"
              data-testid="empty-trash"
              :disabled="trashItems.length === 0"
              @click="emptyTrash"
            >
              Vider la corbeille
            </button>
          </p>
          <ul>
            <li v-for="item in trashItems" :key="item.id" class="row">
              <span>{{ item.label }} · {{ formatBytes(item.size) }} · {{ item.reason }}</span>
              <span class="actions">
                <button type="button" @click="restoreTrash(item.id)">Restaurer</button>
                <button type="button" class="danger" @click="purgeTrash(item)">Supprimer</button>
              </span>
            </li>
          </ul>
    </section>
    <section v-if="section === 'alldebrid'" class="settings-section panel stack">
      <h2 class="settings-section-title">AllDebrid</h2>
      <AllDebridAccountBlock />
      <div class="alldebrid-account divider-line" aria-hidden="true" />
      <form @submit.prevent="saveDebrid">
            <label class="check">
              <input v-model="alldebridAutoPurge" type="checkbox" />
              <span>Purger AllDebrid après un dépôt réussi</span>
            </label>
            <p class="hint">
              Activé par défaut. Quand un fichier est rangé, le lien enregistré, l'entrée d'historique
              correspondante et l'aimant de ce téléchargement sont retirés du compte.
            </p>
            <p v-if="alldebridError" class="error">{{ alldebridError }}</p>
            <p v-if="alldebridMessage">{{ alldebridMessage }}</p>
            <button type="submit">{{ saveSettingsLabel() }}</button>
            <button type="button" @click="purgeDebrid(false)">Purger AllDebrid</button>
            <button type="button" @click="purgeDebrid(true)">Tout purger</button>
          </form>
          <p class="hint">
            « Purger AllDebrid » ne retire que les liens des téléchargements terminés par MediaTracker.
            « Tout purger » demande une confirmation : l'historique récent du compte est vidé en entier.
          </p>
    </section>
    <section v-if="section === 'disque'" class="settings-section panel stack">
      <h2 class="settings-section-title">Disque et sauvegardes</h2>
      <p class="hint settings-lead">
        La base est copiée chaque jour. Tu peux aussi télécharger une sauvegarde sur cet appareil, ou en importer
        une (fichier <code>.db</code>).
      </p>
      <DiskSpaceCard heading />
          <p class="hint">
            Sous le seuil, les téléchargements passent en pause et une notification part. Les copies quotidiennes
            sont dans <code>/config/backups</code>.
          </p>
          <form @submit.prevent="saveDisk">
            <label>
              <span>Espace libre minimum (Go)</span>
              <input v-model.number="diskMinGb" type="number" min="0" max="1024" required />
              <span class="hint">Défaut 5. 0 désactive le garde-fou.</span>
            </label>
            <label>
              <span>Sauvegardes conservées</span>
              <input v-model.number="backupKeep" type="number" min="1" max="90" required />
              <span class="hint">Défaut 7. Une par jour.</span>
            </label>
            <p v-if="diskError" class="error">{{ diskError }}</p>
            <p v-if="diskMessage">{{ diskMessage }}</p>
            <button type="submit">{{ saveSettingsLabel() }}</button>
          </form>
          <DatabaseBackupRestore />
    </section>
    <section v-if="section === 'application'" class="settings-section panel stack">
      <h2 class="settings-section-title">Application</h2>
      <p class="hint">
        Installez MediaTracker sur l'écran d'accueil pour une ouverture plein écran et la file d'ID TMDB hors
        ligne.
      </p>
      <p v-if="!installPrompt" class="hint">
        Installation non proposée par ce navigateur (utilisez le menu du navigateur ou un appareil compatible).
      </p>
      <p class="actions">
        <button type="button" class="primary" :disabled="!installPrompt" @click="openInstallPanel">
          Installer MediaTracker
        </button>
      </p>
    </section>
    <section v-if="section === 'maj'" class="settings-section panel stack">
      <h2 class="settings-section-title">Mise à jour et redémarrage</h2>
      <p class="hint">
            Watchtower vérifie les images toutes les heures ; vous pouvez aussi lancer une mise à jour immédiate
            ici. Les jetons GHCR, GitHub et Watchtower se configurent dans
            <router-link to="/reglages/jetons">Jetons d'API</router-link>.
          </p>
          <div class="settings-update-version panel stack">
            <h3 class="settings-subsection-title">Version installée</h3>
            <dl class="settings-update-dl">
              <div><dt>Version</dt><dd>{{ updateSnapshot.version || "—" }}</dd></div>
              <div><dt>Image</dt><dd>{{ updateSnapshot.currentSha }}</dd></div>
              <div v-if="updateSnapshot.buildDate"><dt>Construite le</dt><dd>{{ updateSnapshot.buildDate }}</dd></div>
              <div v-if="updateSnapshot.checkedAt"><dt>Dernière vérification</dt><dd>{{ updateSnapshot.checkedAt }}</dd></div>
            </dl>
            <p v-if="updateSnapshot.updateAvailable && updateSnapshot.remoteSha" class="settings-update-badge">
              Nouvelle image publiée ({{ updateSnapshot.remoteSha }})
            </p>
          </div>
          <article class="settings-update-now panel stack">
            <h3 class="settings-subsection-title">Mettre à jour maintenant</h3>
            <p class="hint">
              Watchtower télécharge la dernière image et redémarre MediaTracker. Comptez environ une minute
              d'interruption.
            </p>
            <p v-if="watchtowerUnavailableLine" class="hint settings-update-unavail">
              {{ watchtowerUnavailableLine }}
            </p>
            <p class="actions">
              <button
                type="button"
                class="primary settings-update-now-btn"
                :class="{ 'settings-update-now-btn--locked': !watchtowerConfigured }"
                :disabled="updateNowButtonDisabled"
                @click="updateNow"
              >
                {{ updateNowButtonLabel }}
              </button>
            </p>
            <p v-if="updateNowState === 'sent' && restartWaitSeconds > 0" class="settings-update-info">
              {{ updateNowMessage }}
              En attente du redémarrage… {{ formatRestartWait(restartWaitSeconds) }}
            </p>
            <p v-else-if="updateNowState === 'up-to-date'" class="hint settings-update-muted">
              {{ updateNowMessage }}
            </p>
            <p
              v-else-if="updateNowState === 'unreachable' || updateNowState === 'token-refused'"
              class="error settings-update-error"
            >
              {{ updateNowMessage }}
            </p>
            <p v-else-if="updateNowMessage && updateNowState === 'sent'" class="error settings-update-error">
              {{ updateNowMessage }}
            </p>
          </article>
          <p v-if="updateError" class="error">{{ updateError }}</p>
          <p class="actions">
            <button type="button" @click="checkUpdate">Vérifier la mise à jour</button>
            <button type="button" @click="restartApp">Redémarrer</button>
          </p>
          <div class="settings-setrow">
            <div>
              <div class="settings-setrow-label">Me notifier quand Watchtower installe une nouvelle version</div>
              <p class="hint settings-setrow-hint">
                Envoie une notification par les canaux configurés (ntfy, Home Assistant…) après chaque mise à jour
                automatique.
              </p>
              <p v-if="updateNotifySaved" class="settings-saved">✓ Enregistré.</p>
            </div>
            <label class="settings-setrow-switch">
              <span class="switch">
                <input
                  type="checkbox"
                  :checked="notifyOnUpdate"
                  @change="saveNotifyOnUpdate(checkedFrom($event))"
                />
                <span class="switch-ui" aria-hidden="true"></span>
              </span>
            </label>
          </div>
    </section>
    <section v-if="section === 'jetons'" class="settings-section panel stack">
      <h2 class="settings-section-title">Jetons d'API</h2>
      <article class="panel stack" data-testid="outbound-secrets-block">
        <h3 class="settings-subsection-title">Secrets pour services distants</h3>
        <p class="hint">
          Jetons que MediaTracker utilise pour joindre d'autres services (GHCR, GitHub, Watchtower).
          Collez ici la valeur de <code>WATCHTOWER_HTTP_API_TOKEN</code> pour Watchtower — pas un jeton
          « Accès API » ci-dessous. Les secrets ne sont jamais réaffichés après enregistrement.
        </p>
        <form data-testid="outbound-secrets-form" @submit.prevent="saveUpdateTokens">
          <label>
            <span>
              Jeton GHCR (lecture)
              <span class="badge" :class="{ on: ghcrSet }" data-testid="ghcr-secret-status">{{
                ghcrSet ? "enregistré" : "non renseigné"
              }}</span>
            </span>
            <input v-model="ghcrToken" type="password" autocomplete="off" :placeholder="ghcrSet ? 'inchangé' : ''" />
          </label>
          <label>
            <span>
              Jeton GitHub (repli)
              <span class="badge" :class="{ on: githubSet }" data-testid="github-secret-status">{{
                githubSet ? "enregistré" : "non renseigné"
              }}</span>
            </span>
            <input v-model="githubToken" type="password" autocomplete="off" :placeholder="githubSet ? 'inchangé' : ''" />
          </label>
          <h4 class="settings-subsection-title">Watchtower</h4>
          <p class="hint" data-testid="watchtower-url-status">
            URL enregistrée :
            {{ watchtowerUrlStored.trim() ? watchtowerUrlStored : "non renseignée" }}
          </p>
          <label>
            <span>URL de Watchtower</span>
            <input v-model="watchtowerUrl" data-testid="watchtower-url-input" autocomplete="off" />
            <span class="hint">
              Même réseau Docker que MediaTracker, sans exposer de port sur l'hôte. Exemple :
              <code>http://watchtower:8080</code>
            </span>
          </label>
          <label>
            <span>
              Jeton Watchtower
              <span class="badge" :class="{ on: watchtowerTokenSet }" data-testid="watchtower-secret-status">{{
                watchtowerTokenSet ? "enregistré" : "non renseigné"
              }}</span>
            </span>
            <input
              v-model="watchtowerToken"
              type="password"
              autocomplete="off"
              data-testid="watchtower-token-input"
              :placeholder="watchtowerTokenSet ? 'inchangé' : ''"
            />
            <span class="hint">
              Doit correspondre à <code>WATCHTOWER_HTTP_API_TOKEN</code> du conteneur Watchtower.
            </span>
          </label>
          <p v-if="updateError" class="error">{{ updateError }}</p>
          <p v-if="updateTokensMessage" class="settings-saved">{{ updateTokensMessage }}</p>
          <button type="submit">{{ saveSettingsLabel() }}</button>
        </form>
      </article>
      <article class="panel stack" data-testid="api-access-block">
        <h3 class="settings-subsection-title">Accès API</h3>
        <p class="hint">
          Jetons que MediaTracker délivre à des scripts sur le LAN. Ne sert pas pour Watchtower.
          Le secret s'affiche une seule fois. Portées : lecture, écriture, administration.
        </p>
          <form @submit.prevent="createToken">
            <label>
              <span>Nom</span>
              <input v-model="tokenName" required maxlength="120" />
            </label>
            <fieldset class="checks">
              <legend>Portées</legend>
              <label v-for="scope in ['read', 'write', 'admin']" :key="scope" class="check">
                <input
                  type="checkbox"
                  :checked="tokenScopes.includes(scope)"
                  @change="toggleScope(scope, checkedFrom($event))"
                />
                <span>{{ scope }}</span>
              </label>
            </fieldset>
            <p v-if="tokenError" class="error">{{ tokenError }}</p>
            <button type="submit">Créer un jeton</button>
          </form>
          <p v-if="freshToken" class="warn">
            Copiez-le maintenant, il ne sera plus affiché : <code>{{ freshToken }}</code>
          </p>
          <p v-if="!tokens.length" class="hint">Aucun jeton.</p>
          <ul>
            <li v-for="token in tokens" :key="token.id" class="row">
              <span>{{ token.name }} · {{ token.prefix }}… · {{ token.scopes.join(", ") }}</span>
              <button type="button" @click="revokeToken(token.id)">Révoquer</button>
            </li>
          </ul>
      </article>
    </section>
    <section v-if="section === 'reset'" class="settings-section panel stack">
      <h2 class="settings-section-title">Réinitialiser la base</h2>
      <p class="hint">
            Efface les fiches, les téléchargements et l'index de la bibliothèque. Une copie SQLite est écrite
            dans le dossier backups avant toute suppression. Les fichiers sur le disque restent.
          </p>
          <form @submit.prevent="resetDatabase">
            <label>
              <span>Confirmation</span>
              <input v-model="resetConfirm" autocomplete="off" placeholder="RESET" />
            </label>
            <p v-if="resetError" class="error">{{ resetError }}</p>
            <p v-if="resetMessage" class="hint">{{ resetMessage }}</p>
            <button type="submit" :disabled="resetting">Réinitialiser</button>
          </form>
    </section>
  </div>
</template>

<style scoped>
.cleanup-days-muted {
  opacity: 0.55;
}
</style>

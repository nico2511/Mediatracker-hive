<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import StatusIcon from "../components/StatusIcon.vue";
import TmdbPosterImg from "../components/TmdbPosterImg.vue";
import DeleteFicheDialog from "../components/DeleteFicheDialog.vue";
import FicheMaintenance from "../components/FicheMaintenance.vue";
import FicheStateActions from "../components/FicheStateActions.vue";
import FicheHideMenuEntry from "../components/FicheHideMenuEntry.vue";
import TrailerPlayerDialog from "../components/TrailerPlayerDialog.vue";
import { trailerSubtitle } from "../trailerSubtitle";
import FilmFileBlock from "../components/FilmFileBlock.vue";
import FilmReleaseDatesBlock from "../components/FilmReleaseDatesBlock.vue";
import ProfileReplacementBanner from "../components/ProfileReplacementBanner.vue";
import LineMoreActions from "../components/LineMoreActions.vue";
import SourceRank from "../components/SourceRank.vue";
import {ApiError, api, userFacingApiError} from "../api";
import { removeMediaFromListSnapshot } from "../pwa/offlineSnapshot";
import TmdbIdChangeDialog from "../components/TmdbIdChangeDialog.vue";
import type { TmdbIdChangePreview } from "../tmdbIdChange";
import { readTmdbIdChangePreview } from "../tmdbIdChange";
import {
  describeEffectiveChain,
  GLOBAL_ALLOW_FALLBACK_KEY,
  moveCustomFallback,
  parseProfileFallbackOrder,
  PROFILE_FALLBACK_ORDER_KEY,
  qualityProfileSummary,
  resolveEffectiveChainIds,
  toggleCustomFallbackSelection,
  type QualityProfileLike,
} from "../profileFallback";
import { fetchSettings } from "../settings";
import { applySettingNumber } from "../settingsMeta";
import {
  absentWithoutSourceCount,
  absentWithoutSourceLabel,
  catalogEpisodeCount,
  missingCatalogLabel,
  missingCatalogSlots,
  onDiskTone,
  presentOnDiskCount,
  readyWithoutFileCount,
  readyWithoutFileLabel,
  seasonProgressDetail,
  watchedCount,
  watchedTone,
} from "../fiche/seasonCounts";
import { useProgressStream, type ProgressPayload } from "../composables/useProgressStream";
import { fileStatusIcon, fileStatusLabel } from "../fileStatus";
import { formatParis } from "../time";
import { useHiveEnabled } from "../composables/useHive";
import { tmdbBackdropSrc, tmdbPosterSrc } from "../tmdbImages";
import { tmdbRatingLabel, tmdbVoteCountPhrase } from "../tmdbRatingLabel";
import HiveContextMenuItems from "../components/HiveContextMenuItems.vue";
import HiveMediaShareRow from "../components/HiveMediaShareRow.vue";
import HiveMediaRequestCard from "../components/hive/HiveMediaRequestCard.vue";
import HiveLocalCatalogueBlock from "../components/hive/HiveLocalCatalogueBlock.vue";
import { provideHiveSeriesCatalogue } from "../composables/useHiveSeriesCatalogue";
import type { HiveMyRequestItem } from "../hive/hiveRequests";
import HiveBulkShareMenuItems from "../components/HiveBulkShareMenuItems.vue";
import HiveSeriesShareSummary from "../components/HiveSeriesShareSummary.vue";
import { useHiveSharePolling } from "../composables/useHiveSharePolling";
import type { HiveShareUiState } from "../hive/hiveBadges";
import { hiveEpisodeShareChips, type HiveEpisodeShareChip } from "../hive/hiveEpisodeShareLabel";

interface MediaRow {
  id: number;
  type: string;
  title: string;
  year: number | null;
  overview: string | null;
  poster_url: string | null;
  backdrop_url: string | null;
  tmdb_id: number | null;
  tmdb_url: string | null;
  jellyfin_web_url?: string | null;
  identity_edited_at?: string | null;
  series_status: string | null;
  followed: boolean;
  on_hold: boolean;
  auto_download: boolean;
  new_episode_policy: string;
  auto_status: string;
  pref_quality: string;
  pref_language: string;
  allow_fallback: boolean;
  fallback_profile_ids: number[] | null;
  source_order: string | null;
  quality_profile_id: number | null;
  scan_interval_min: number | null;
  keep: boolean;
  auto_delete: boolean;
  auto_delete_days: number | null;
  cleanup_reason?: string | null;
  poll: {
    allowed: boolean;
    reason: string;
    last_check_at: string | null;
    next_check_at: string | null;
    last_result: string | null;
    last_message: string | null;
    interval_min: number;
  };
  next_episode: { season: number | null; episode: number | null; title: string | null; air_date: string | null } | null;
  progress: { watched: number; total: number };
  available_not_downloaded: number;
  source_versions?: { title: string; season: number | null; quality: string | null; language: string | null; kept: boolean; reason: string }[];
  attached_count?: number;
  file_status?: string;
  file_absent?: boolean;
  on_disk?: boolean;
  film_not_yet_available?: boolean;
  film_status_line?: string | null;
  film_availability_source?: string | null;
  next_air_date?: string | null;
  tmdb_release_by_region?: Record<string, { theatrical?: string | null; digital?: string | null; physical?: string | null }> | null;
  tmdb_trailer_youtube_key?: string | null;
  tmdb_trailer_kind?: string | null;
  tmdb_trailer_language?: string | null;
  tmdb_vote_average?: number | null;
  tmdb_vote_count?: number | null;
  hidden_at?: string | null;
}

interface Episode {
  id: number;
  season: number | null;
  episode: number | null;
  state: string;
  title: string | null;
  air_date: string | null;
  downloaded: boolean;
  watched: boolean;
  watch_source?: string;
  source_available: boolean;
  download_active: boolean;
  download_busy?: boolean;
  keep: boolean;
  filename?: string | null;
  original_name?: string | null;
  file_status?: string;
  file_absent?: boolean;
  trash_entry_id?: number | null;
  served_profile?: { profile_id: number; profile_name: string; repli: boolean } | null;
  profile_replacement?: {
    id: number;
    principal_profile_id: number;
    principal_profile_name: string;
    repli_profile_id: number;
    repli_profile_name: string;
  } | null;
}

interface Season {
  number: number | null;
  name: string | null;
  air_date: string | null;
  episode_count: number | null;
  keep: boolean;
  file_status?: string;
  file_status_counts?: { present: number; trash: number; absent: number; downloading: number };
  episodes: Episode[];
}

const route = useRoute();
const router = useRouter();
const reglagesOpen = ref(route.query.reglages === "ouverts");
const suiviOpen = ref(route.query.suivi === "ouvert");
const openSeasonFromQuery = ref(route.query.saison === "ouverte" || route.query.saison === "1");
const ficheDelete = ref(false);
const hideJustDone = ref(false);
const media = ref<MediaRow | null>(null);
const heroTrailerOpen = ref(false);
const heroTrailerSubtitle = computed(() =>
  media.value ? trailerSubtitle(media.value.tmdb_trailer_kind, media.value.tmdb_trailer_language) : null,
);
const seasons = ref<Season[]>([]);
const trashOnUnfollow = ref(false);
const slotDraft = ref<Record<number, { season: string; episode: string }>>({});
const error = ref("");
const message = ref("");
const tmdbDraft = ref("");
const tmdbChangeOpen = ref(false);
const tmdbChangePreview = ref<TmdbIdChangePreview | null>(null);
const tmdbChangePendingId = ref<number | null>(null);
const tmdbChangeBusy = ref(false);
interface IdentityRow {
  id: number;
  source: string;
  old_tmdb: number | null;
  new_tmdb: number | null;
  changed_at: string;
  undone_at: string | null;
}
const identity = ref<IdentityRow[]>([]);
const checking = ref(false);
const prefs = ref({
  quality: "HD",
  language: "VFF",
  fallbackMode: "global" as "global" | "custom",
  customFallbackIds: [] as number[],
  interval: "",
  sourceOrder: "",
  profileId: "",
});
/** Ordre global (lecture seule) : affiché quand la fiche n’a pas de surcharge. */
const globalDownloadSourceOrder = ref("");
const availableDownloadSourceIds = ref<string[]>([]);
const downloadSourcesReady = ref(true);
const downloadSourcesBlockedMessage = ref("");
interface QualityProfileOption extends QualityProfileLike {
  is_default: boolean;
  exclude_keywords: string[];
}

const qualityProfiles = ref<QualityProfileOption[]>([]);
const globalFallbackOrderIds = ref<number[]>([]);
const globalAllowFallback = ref(false);
const catalogRegion = ref("FR");
const prefsContextStatus = ref<"loading" | "ready" | "error">("loading");
const prefsContextError = ref("");
const keepMedia = ref(false);
const autoDays = ref("");
const globalAutoDeleteDays = ref<number | null>(null);
const filmItem = ref<Episode | null>(null);
const { hiveEnabled } = useHiveEnabled();
const hiveSeriesCatalogue = provideHiveSeriesCatalogue(
  computed(() => media.value?.tmdb_id ?? null),
  computed(() => hiveEnabled.value && media.value?.type === "series"),
);
const hiveShare = ref<HiveShareUiState | null>(null);
const hiveItemRequest = ref<{ announced: boolean; promptAvailable: boolean; retracted: boolean } | null>(null);
const hiveSeriesRequests = ref<HiveMyRequestItem[]>([]);
const hiveEpisodeShares = ref<Record<number, HiveShareUiState>>({});
const hiveEmpreintesSignal = ref(0);

function hiveChipsForEpisode(itemId: number): HiveEpisodeShareChip[] {
  const state =
    hiveEpisodeShares.value[itemId] ??
    (primaryItemId.value === itemId ? hiveShare.value : null);
  return hiveEpisodeShareChips(state);
}

function episodeFileOnDisk(episodeRow: Episode): boolean {
  return episodeRow.file_status === "present";
}

function forceRedownloadLabel(compact: boolean, onDisk: boolean): string {
  if (!onDisk) {
    return "Forcer le téléchargement";
  }
  return compact ? "Retélécharger" : "Retélécharger (remplace le fichier)";
}

function seasonShareLabel(group: Season): string | null {
  const withFile = group.episodes.filter((ep) => hiveEpisodeShares.value[ep.id]?.has_file);
  if (!withFile.length) {
    return null;
  }
  const shared = withFile.filter((ep) => {
    const s = hiveEpisodeShares.value[ep.id];
    return s?.shared && !s.exclude_all;
  }).length;
  return `${shared}/${withFile.length} partagés`;
}

async function refreshHiveEpisodeShares() {
  if (!hiveEnabled.value || !media.value || media.value.type !== "series") {
    hiveEpisodeShares.value = {};
    return;
  }
  const ids = seasons.value.flatMap((group) => group.episodes.map((ep) => ep.id));
  if (!ids.length) {
    return;
  }
  try {
    const batch = await api<(HiveShareUiState & { media_item_id: number })[]>(
      `/api/v1/hive/items/sharing?ids=${ids.join(",")}`,
    );
    const next: Record<number, HiveShareUiState> = {};
    for (const row of batch) {
      next[row.media_item_id] = row;
    }
    hiveEpisodeShares.value = next;
  } catch {
    hiveEpisodeShares.value = {};
  }
}

const primaryItemId = computed((): number | null => {
  if (filmItem.value?.id) {
    return filmItem.value.id;
  }
  for (const group of seasons.value) {
    for (const episode of group.episodes) {
      if (episode.id) {
        return episode.id;
      }
    }
  }
  return null;
});

const ficheVisible = ref(typeof document !== "undefined" ? document.visibilityState === "visible" : true);

function onFicheVisibility() {
  ficheVisible.value = document.visibilityState === "visible";
  if (ficheVisible.value) {
    hiveSharePolling.resumeIfPaused();
  } else {
    hiveSharePolling.onFicheHidden();
  }
}

const hiveSharePolling = useHiveSharePolling({
  mediaItemId: primaryItemId,
  hiveEnabled,
  state: hiveShare,
  visible: () => ficheVisible.value,
});

async function refreshHiveItemRequest() {
  hiveItemRequest.value = null;
  hiveSeriesRequests.value = [];
  if (!hiveEnabled.value || !media.value?.followed) {
    return;
  }
  try {
    const resp = await api<{ items: HiveMyRequestItem[] }>("/api/v1/hive/requests/mine");
    const items = resp.items ?? [];
    if (media.value.type === "series") {
      hiveSeriesRequests.value = items.filter((entry) => entry.media_id === media.value!.id);
      return;
    }
    if (primaryItemId.value == null) {
      return;
    }
    const itemRow = filmItem.value;
    if (itemRow?.downloaded || itemRow?.watched) {
      return;
    }
    const row = items.find((entry) => entry.media_item_id === primaryItemId.value);
    if (!row) {
      return;
    }
    hiveItemRequest.value = {
      announced: row.status === "pending" || row.status === "transfer",
      promptAvailable: row.status === "prompt" && Boolean(row.hive_request_available),
      retracted: row.status === "retracted",
    };
  } catch {
    hiveItemRequest.value = null;
    hiveSeriesRequests.value = [];
  }
}

async function onHiveSeriesShareChanged() {
  await onHiveShareMenuChanged();
  await refreshHiveItemRequest();
}

async function refreshHiveShareState() {
  await hiveSharePolling.reloadFromNavigation();
}

watch(primaryItemId, () => {
  void refreshHiveItemRequest();
});

watch(primaryItemId, (itemId, prev) => {
  if (prev == null || itemId === prev) {
    return;
  }
  hiveSharePolling.onItemIdChanged();
  if (hiveEnabled.value && itemId != null) {
    void refreshHiveShareState();
  }
});

watch(hiveEnabled, (enabled, prev) => {
  if (!enabled) {
    hiveShare.value = null;
    hiveSharePolling.stopPolling();
    return;
  }
  if (prev === false && primaryItemId.value != null) {
    void refreshHiveShareState();
  }
});

async function onHiveShareMenuChanged() {
  const itemId = primaryItemId.value;
  if (itemId == null) {
    return;
  }
  await hiveSharePolling.afterMutationFetch();
}

onUnmounted(() => {
  document.removeEventListener("visibilitychange", onFicheVisibility);
  hiveSharePolling.stopPolling();
});

const seriesStatus: Record<string, string> = {
  returning: "En cours",
  ended: "Terminée",
  canceled: "Annulée",
  planned: "Prévue",
  in_production: "En production",
  unknown: "Inconnu",
};

const mediaId = computed((): number | null => {
  const raw = route.params.id;
  const text = Array.isArray(raw) ? raw[0] : raw;
  if (!text) {
    return null;
  }
  const numeric = Number(text);
  return Number.isFinite(numeric) ? numeric : null;
});

async function loadPrefsContext() {
  prefsContextStatus.value = "loading";
  prefsContextError.value = "";
  try {
    const [settingsBody, profilesBody] = await Promise.all([
      fetchSettings(),
      api<{ profiles: QualityProfileOption[] }>("/api/v1/quality-profiles"),
    ]);
    globalAutoDeleteDays.value = applySettingNumber(settingsBody.settings, "auto_delete_days") ?? null;
    const orderRow = settingsBody.settings.find((row) => row.key === PROFILE_FALLBACK_ORDER_KEY);
    globalFallbackOrderIds.value = parseProfileFallbackOrder(
      orderRow && typeof orderRow.value === "string" ? orderRow.value : "",
    );
    const allowRow = settingsBody.settings.find((row) => row.key === GLOBAL_ALLOW_FALLBACK_KEY);
    globalAllowFallback.value = allowRow?.value === true;
    const regionRow = settingsBody.settings.find((row) => row.key === "tmdb_region");
    if (regionRow && typeof regionRow.value === "string" && regionRow.value.trim()) {
      catalogRegion.value = regionRow.value.trim().toUpperCase();
    }
    qualityProfiles.value = profilesBody.profiles;
    prefsContextStatus.value = "ready";
  } catch (err) {
    prefsContextStatus.value = "error";
    prefsContextError.value = userFacingApiError(err, "Chargement des profils impossible.");
  }
}

onMounted(async () => {
  document.addEventListener("visibilitychange", onFicheVisibility);
  await Promise.all([loadPrefsContext(), reload()]);
});

const autoDaysPlaceholder = computed(() =>
  globalAutoDeleteDays.value === null ? "global" : `global (${globalAutoDeleteDays.value})`,
);

const conservationRetentionHint = computed(() => {
  if (!media.value?.cleanup_reason || keepMedia.value) {
    return "";
  }
  if (media.value.type === "film" && media.value.progress.watched > 0) {
    return "";
  }
  return "Le délai de suppression ne démarre qu'après un visionnage Jellyfin ou un marquage « vu ».";
});

function syncFilmItemFromSeasons() {
  filmItem.value = null;
  if (media.value?.type === "film") {
    for (const group of seasons.value) {
      const row = group.episodes.find((episode) => episode.downloaded) ?? group.episodes[0];
      if (row) {
        filmItem.value = row;
        break;
      }
    }
  }
}

function applyFicheStream(payload: ProgressPayload) {
  if (!media.value || document.visibilityState !== "visible") {
    return;
  }
  const fiche = payload.fiches?.[String(media.value.id)];
  if (!fiche) {
    return;
  }
  media.value = fiche.media as unknown as MediaRow;
  seasons.value = fiche.seasons as Season[];
  syncFilmItemFromSeasons();
}

useProgressStream(applyFicheStream, { mediaId });

async function reload() {
  const id = route.params.id;
  media.value = await api<MediaRow>(`/api/v1/media/${id}`);
  tmdbDraft.value = media.value.tmdb_id == null ? "" : String(media.value.tmdb_id);
  try {
    identity.value = await api<IdentityRow[]>(`/api/v1/media/${id}/identity`);
  } catch {
    identity.value = [];
  }
  globalDownloadSourceOrder.value = "";
  if (!(media.value.source_order || "").trim()) {
    try {
      const settings = await fetchSettings();
      const row = settings.settings.find((item) => item.key === "download_source_order");
      if (row && typeof row.value === "string" && row.value.trim()) {
        globalDownloadSourceOrder.value = row.value.trim();
      }
    } catch {
      globalDownloadSourceOrder.value = "";
    }
  }
  const chainRaw = media.value.fallback_profile_ids;
  prefs.value = {
    quality: media.value.pref_quality,
    language: media.value.pref_language,
    fallbackMode: chainRaw == null ? "global" : "custom",
    customFallbackIds: chainRaw == null ? [] : [...chainRaw],
    interval: media.value.scan_interval_min == null ? "" : String(media.value.scan_interval_min),
    sourceOrder: media.value.source_order || "",
    profileId: media.value.quality_profile_id == null ? "" : String(media.value.quality_profile_id),
  };
  keepMedia.value = media.value.keep;
  autoDays.value = media.value.auto_delete_days == null ? "" : String(media.value.auto_delete_days);
  const body = await api<{ seasons: Season[] }>(`/api/v1/media/${id}/items`);
  seasons.value = body.seasons;
  syncFilmItemFromSeasons();
  rememberSeasons();
  await loadLibraryFolders();
  await loadJellyfin();
  await refreshHiveShareState();
  await refreshHiveItemRequest();
  await refreshHiveEpisodeShares();
  try {
    const status = await api<{
      available_sources?: string[];
      download_ready?: boolean;
      blocked_message?: string | null;
    }>("/api/v1/settings/sources/status");
    availableDownloadSourceIds.value = Array.isArray(status.available_sources) ? status.available_sources : [];
    downloadSourcesReady.value = status.download_ready !== false;
    downloadSourcesBlockedMessage.value = (status.blocked_message || "").trim();
  } catch {
    availableDownloadSourceIds.value = [];
    downloadSourcesReady.value = true;
    downloadSourcesBlockedMessage.value = "";
  }
}

const showDetails = ref(false);

function formatDateTime(value: string | null): string {
  return formatParis(value);
}

async function checkNow() {
  if (!media.value) {
    return;
  }
  checking.value = true;
  error.value = "";
  message.value = "";
  try {
    await api<{ message: string }>(`/api/v1/media/${media.value.id}/check`, { method: "POST" });
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Vérification impossible.");
  } finally {
    checking.value = false;
  }
}

function sourceOrderForApi(): string | null {
  const local = prefs.value.sourceOrder.trim();
  if (!local) {
    return null;
  }
  const global = globalDownloadSourceOrder.value.trim();
  if (global && local === global) {
    return null;
  }
  return local;
}

async function savePrefs() {
  if (!media.value) {
    return;
  }
  if (prefsContextStatus.value !== "ready") {
    error.value = "Les profils ne sont pas encore chargés.";
    return;
  }
  error.value = "";
  message.value = "";
  const interval = prefs.value.interval.trim();
  try {
    const fallbackPayload =
      prefs.value.fallbackMode === "global" ? null : prefs.value.customFallbackIds;
    await api(`/api/v1/media/${media.value.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        fallback_profile_ids: fallbackPayload,
        source_order: sourceOrderForApi(),
        quality_profile_id: prefs.value.profileId ? Number(prefs.value.profileId) : null,
        scan_interval_min: interval === "" ? null : Number(interval),
      }),
    });
    message.value = "Préférences enregistrées.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

function closeTmdbChangeDialog() {
  tmdbChangeOpen.value = false;
  tmdbChangePreview.value = null;
  tmdbChangePendingId.value = null;
}

async function applyTmdbPatch(
  parsed: number,
  confirm?: "keep_episodes" | "rebuild_episodes",
) {
  if (!media.value) {
    return;
  }
  const body: { tmdb_id: number; tmdb_id_confirm?: string } = { tmdb_id: parsed };
  if (confirm) {
    body.tmdb_id_confirm = confirm;
  }
  const updated = (await api(`/api/v1/media/${media.value.id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  })) as { tmdb_refresh_warning?: string | null };
  if (updated.tmdb_refresh_warning) {
    message.value = updated.tmdb_refresh_warning;
  } else if (confirm === "rebuild_episodes") {
    message.value = "Identifiant mis à jour : épisodes reconstruits, fichiers à réassocier si besoin.";
  } else {
    message.value = "Fiche identifiée : métadonnées mises à jour. Poussée Jellyfin en file.";
  }
  await reload();
}

async function saveTmdb() {
  if (!media.value) {
    return;
  }
  const parsed = Number(tmdbDraft.value.trim());
  if (!Number.isInteger(parsed) || parsed < 1) {
    error.value = "Identifiant TMDB invalide.";
    return;
  }
  if (parsed === media.value.tmdb_id) {
    message.value = "Identifiant inchangé.";
    return;
  }
  error.value = "";
  message.value = "";
  try {
    await applyTmdbPatch(parsed);
  } catch (err) {
    const problem = err instanceof ApiError ? err.problem : (err as { problem?: Record<string, unknown> })?.problem;
    const preview = readTmdbIdChangePreview(problem);
    if ((err as ApiError)?.status === 409 && preview) {
      tmdbChangePreview.value = preview;
      tmdbChangePendingId.value = parsed;
      tmdbChangeOpen.value = true;
      return;
    }
    error.value = userFacingApiError(err, "Correction impossible.");
  }
}

async function confirmTmdbChange(confirm: "keep_episodes" | "rebuild_episodes") {
  if (!media.value || tmdbChangePendingId.value == null) {
    return;
  }
  error.value = "";
  message.value = "";
  tmdbChangeBusy.value = true;
  try {
    await applyTmdbPatch(tmdbChangePendingId.value, confirm);
    closeTmdbChangeDialog();
  } catch (err) {
    error.value = userFacingApiError(err, "Correction impossible.");
  } finally {
    tmdbChangeBusy.value = false;
  }
}

async function undoIdentity(changeId: number) {
  if (!media.value) {
    return;
  }
  if (!window.confirm("Annuler cette modification d'identifiant ?")) {
    return;
  }
  error.value = "";
  message.value = "";
  try {
    await api(`/api/v1/media/${media.value.id}/identity/${changeId}/undo`, { method: "POST" });
    message.value = "Modification annulée. La poussée vers Jellyfin est en file.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Annulation impossible.");
  }
}

async function saveRetention() {
  if (!media.value) {
    return;
  }
  error.value = "";
  message.value = "";
  const days = autoDays.value.trim();
  try {
    await api(`/api/v1/media/${media.value.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        keep: keepMedia.value,
        auto_delete_days: days === "" ? null : Number(days),
      }),
    });
    message.value = "Conservation enregistrée.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Enregistrement impossible.");
  }
}

async function setEpisodeKeep(itemId: number, keep: boolean) {
  error.value = "";
  await api(`/api/v1/items/${itemId}`, { method: "PATCH", body: JSON.stringify({ keep }) });
  await reload();
}

const seasonNotice = ref<Record<string, string>>({});

async function markWatched(body: Record<string, unknown>) {
  if (!media.value) return;
  error.value = "";
  const seasonKey =
    typeof body.season === "number" ? seasonStorageKey(body.season) : null;
  try {
    const result = await api<{ message: string }>(`/api/v1/media/${media.value.id}/watch`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    message.value = result.message;
    if (seasonKey) {
      seasonNotice.value = { ...seasonNotice.value, [seasonKey]: result.message };
    }
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Marquage impossible.");
  }
}

async function markSeasonWatched(season: number, undo = false) {
  await markWatched({ season, undo });
}

async function setMediaKeep(keep: boolean) {
  if (!media.value) {
    return;
  }
  error.value = "";
  await api(`/api/v1/media/${media.value.id}`, { method: "PATCH", body: JSON.stringify({ keep }) });
  await reload();
}

function seasonKeepEffective(group: Season): boolean {
  return Boolean(media.value?.keep || group.keep);
}

function episodeKeepEffective(group: Season, episodeRow: Episode): boolean {
  return Boolean(media.value?.keep || group.keep || episodeRow.keep);
}

function trashNeedsConfirm(scope: "media" | "season" | "episode", extra: { season?: number; item_id?: number }): boolean {
  if (!media.value) {
    return true;
  }
  if (media.value.keep) {
    return true;
  }
  if (scope === "media") {
    return false;
  }
  if (scope === "season") {
    const group = seasons.value.find((row) => row.number === extra.season);
    return Boolean(group && seasonKeepEffective(group));
  }
  for (const group of seasons.value) {
    const episodeRow = group.episodes.find((row) => row.id === extra.item_id);
    if (episodeRow) {
      return episodeKeepEffective(group, episodeRow);
    }
  }
  return false;
}

async function setSeasonKeep(number: number, keep: boolean) {
  if (!media.value) {
    return;
  }
  error.value = "";
  await api(`/api/v1/media/${media.value.id}/seasons/${number}`, {
    method: "PATCH",
    body: JSON.stringify({ keep }),
  });
  await reload();
}

async function removeFiche(trash: boolean) {
  if (!media.value) {
    return;
  }
  const id = media.value.id;
  ficheDelete.value = false;
  error.value = "";
  message.value = "";
  try {
    await api(`/api/v1/media/${id}?trash=${trash ? "true" : "false"}`, { method: "DELETE" });
    removeMediaFromListSnapshot(id);
    await router.push("/medias");
  } catch (err) {
    error.value = userFacingApiError(err, "Suppression de la fiche impossible.");
  }
}

async function deleteFiles(scope: "media" | "season" | "episode", extra: { season?: number; item_id?: number }) {
  if (!media.value) {
    return;
  }
  const needsConfirm = trashNeedsConfirm(scope, extra);
  const label =
    scope === "media" ? "tous les fichiers de cette fiche" : scope === "season" ? "les fichiers de cette saison" : "ce fichier";
  const prompt = needsConfirm
    ? `« Garder » est actif. Mettre ${label} à la corbeille quand même ? Le suivi et l'état « vu » sont conservés.`
    : `Mettre ${label} à la corbeille ? Le suivi et l'état « vu » sont conservés.`;
  if (!window.confirm(prompt)) {
    return;
  }
  error.value = "";
  message.value = "";
  try {
    const result = await api<{ bytes: number }>("/api/v1/cleanup/files", {
      method: "POST",
      body: JSON.stringify({ media_id: media.value.id, scope, confirm: needsConfirm, ...extra }),
    });
    message.value = `Mis à la corbeille (${result.bytes} octets).`;
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Suppression impossible.");
  }
}

async function queueEpisode(itemId: number) {
  error.value = "";
  message.value = "";
  try {
    await api("/api/v1/downloads", { method: "POST", body: JSON.stringify({ media_item_id: itemId }) });
    message.value = "Ajouté à la file.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Téléchargement refusé.");
  }
}

interface ForceResult {
  queued: number;
  skipped_busy: number;
  needs_confirm: boolean;
  local: { item_id: number; season: number | null; episode: number | null; path: string }[];
  message: string;
}

async function forceDownload(body: { item_id?: number; season?: number }, confirm = false) {
  if (!media.value) {
    return;
  }
  error.value = "";
  message.value = "";
  try {
    const result = await api<ForceResult>(`/api/v1/media/${media.value.id}/force-download`, {
      method: "POST",
      body: JSON.stringify({ ...body, confirm }),
    });
    if (result.needs_confirm && !confirm) {
      const names = result.local.map((row) => row.path.split("/").pop()).filter(Boolean).join(", ");
      const ask = result.local.length > 1
        ? `${result.local.length} épisodes sont déjà sur disque (${names}). Les télécharger quand même ?`
        : result.message;
      if (!window.confirm(ask)) {
        message.value = result.queued
          ? result.message
          : "Téléchargement non forcé : le fichier est déjà sur disque.";
        if (result.queued) {
          await reload();
        }
        return;
      }
      await forceDownload(body, true);
      return;
    }
    message.value = result.message;
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Téléchargement forcé refusé.");
  }
}

function checkedFrom(event: Event): boolean {
  return event.target instanceof HTMLInputElement && event.target.checked;
}

function slot(seasonNumber: number | null, episodeNumber: number | null): string {
  if (seasonNumber == null || episodeNumber == null) {
    return "";
  }
  return `S${String(seasonNumber).padStart(2, "0")}E${String(episodeNumber).padStart(2, "0")}`;
}

function seasonTitle(group: Season): string {
  if (group.number == null) {
    return media.value?.type === "series" ? "À classer (hors saison)" : "Film";
  }
  if (group.number === 0) {
    return group.name || "Spéciaux";
  }
  return group.name || `Saison ${group.number}`;
}

function formatDate(value: string | null): string {
  if (!value) {
    return "";
  }
  const [year, month, day] = value.split("-");
  if (!day || !month || !year) {
    return value;
  }
  return `${day}/${month}/${year}`;
}

async function followAgain() {
  if (!media.value) {
    return;
  }
  error.value = "";
  await api(`/api/v1/media/${media.value.id}`, {
    method: "PATCH",
    body: JSON.stringify({ followed: true, on_hold: false, new_episode_policy: "auto" }),
  });
  await reload();
}

function openExternalTab(url: string) {
  window.open(url, "_blank", "noopener,noreferrer");
}

function ensureDraft(id: number, field: "season" | "episode", value: string) {
  const current = slotDraft.value[id] || { season: "", episode: "" };
  slotDraft.value = { ...slotDraft.value, [id]: { ...current, [field]: value } };
}

async function setAuto(enabled: boolean) {
  if (!media.value) {
    return;
  }
  error.value = "";
  await api(`/api/v1/media/${media.value.id}`, {
    method: "PATCH",
    body: JSON.stringify({ new_episode_policy: enabled ? "auto" : "propose" }),
  });
  await reload();
}

async function setPause(paused: boolean) {
  if (!media.value) {
    return;
  }
  error.value = "";
  await api(`/api/v1/media/${media.value.id}`, {
    method: "PATCH",
    body: JSON.stringify({ on_hold: paused }),
  });
  await reload();
}

async function hideFiche() {
  if (!media.value || media.value.hidden_at) {
    return;
  }
  const extra = media.value.followed
    ? " Le suivi sera aussi arrêté ; fichiers et historique inchangés."
    : "";
  if (
    !window.confirm(
      `Masquer « ${media.value.title} » ?${extra} La fiche disparaît de Médias, de l'Accueil et des suggestions.`,
    )
  ) {
    return;
  }
  error.value = "";
  try {
    await api(`/api/v1/media/${media.value.id}/hide`, { method: "POST" });
    hideJustDone.value = true;
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Masquage impossible.");
  }
}

async function unhideFiche() {
  if (!media.value?.hidden_at) {
    return;
  }
  error.value = "";
  try {
    await api(`/api/v1/media/${media.value.id}/unhide`, { method: "POST" });
    hideJustDone.value = false;
    message.value = "Fiche réaffichée.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Réaffichage impossible.");
  }
}

async function undoHideFiche() {
  await unhideFiche();
  if (!error.value) {
    message.value = "Masquage annulé.";
  }
}

async function unfollow() {
  if (!media.value) {
    return;
  }
  const extra = trashOnUnfollow.value ? " Les fichiers iront à la corbeille." : " Les fichiers et l'historique « vu » restent.";
  if (!window.confirm(`Ne plus suivre « ${media.value.title} » ?${extra}`)) {
    return;
  }
  error.value = "";
  try {
    await api(`/api/v1/media/${media.value.id}/unfollow`, {
      method: "POST",
      body: JSON.stringify({ trash: trashOnUnfollow.value }),
    });
    message.value = "Suivi arrêté.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Arrêt du suivi impossible.");
  }
}

async function assignSlot(itemId: number) {
  const draft = slotDraft.value[itemId];
  if (!draft) {
    return;
  }
  error.value = "";
  await api(`/api/v1/items/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify({ season: Number(draft.season), episode: Number(draft.episode) }),
  });
  await reload();
}

interface LibraryChoice {
  path: string;
  name: string;
  year: number | null;
  tmdb_id: number | null;
  score: number;
  file_count: number;
}

interface JellyfinEpisode {
  item_id: number;
  season: number | null;
  episode: number | null;
  matched: boolean;
  played: boolean;
  reason: string;
}

interface JellyfinDiag {
  configured: boolean;
  series_id: string | null;
  series_ids?: string[];
  series_name: string | null;
  manual_series_id?: string | null;
  synced_at: string | null;
  detail: string;
  summary?: string | null;
  episodes: JellyfinEpisode[];
  unmatched?: JellyfinEpisode[];
}

interface JellyfinSeriesHit {
  id: string;
  name: string;
  year: number | null;
  original_title?: string | null;
}

const libraryFolders = ref<LibraryChoice[]>([]);
const showFolders = ref(false);
const jellyfin = ref<JellyfinDiag | null>(null);
const jellyfinFailed = ref(false);

type JellyfinBucket = "problem" | "present" | "watched";

interface JellyfinRow extends JellyfinEpisode {
  bucket: JellyfinBucket;
}

const jellyfinBuckets = computed(() => {
  const episodes = jellyfin.value?.episodes ?? [];
  const unmatched = jellyfin.value?.unmatched ?? [];
  const watched = episodes.filter((row) => row.played).map((row) => ({ ...row, bucket: "watched" as const }));
  const present = episodes.filter((row) => !row.played).map((row) => ({ ...row, bucket: "present" as const }));
  const problem = unmatched.map((row) => ({ ...row, bucket: "problem" as const }));
  return [
    { key: "problem" as const, title: "Non rapprochés", rows: problem },
    { key: "present" as const, title: "Présents", rows: present },
    { key: "watched" as const, title: "Vus", rows: watched },
  ];
});

const jellyfinChips = computed(() => {
  const count = (key: JellyfinBucket) => jellyfinBuckets.value.find((group) => group.key === key)?.rows.length ?? 0;
  const watched = count("watched");
  const present = count("present");
  const problem = count("problem");
  return [
    { key: "watched", label: watched === 1 ? "1 vu" : `${watched} vus`, count: watched },
    { key: "present", label: present === 1 ? "1 présent" : `${present} présents`, count: present },
    { key: "problem", label: problem === 1 ? "1 non rapproché" : `${problem} non rapprochés`, count: problem },
  ].filter((chip) => chip.count > 0);
});

function jellyfinSeasons(rows: JellyfinRow[]) {
  const bySeason = new Map<number, JellyfinRow[]>();
  for (const row of rows) {
    const season = row.season ?? 0;
    const list = bySeason.get(season) ?? [];
    list.push(row);
    bySeason.set(season, list);
  }
  return [...bySeason.entries()]
    .sort((left, right) => left[0] - right[0])
    .map(([season, items]) => ({
      season,
      items: [...items].sort((left, right) => (left.episode ?? 0) - (right.episode ?? 0) || left.item_id - right.item_id),
    }));
}
const jellyfinQuery = ref("");
const jellyfinHits = ref<JellyfinSeriesHit[]>([]);
const jellyfinLinkError = ref("");
const seasonOpen = ref<Record<string, boolean>>({});

function seasonStorageKey(number: number | null): string {
  return `mt-season:${route.params.id}:${number ?? "film"}`;
}

function fullyWatched(group: Season): boolean {
  return group.episodes.length > 0 && group.episodes.every((row) => row.watched);
}

function seasonProgressLabel(group: Season): string {
  return seasonProgressDetail(group);
}

function rememberSeasons() {
  const next: Record<string, boolean> = {};
  for (const group of seasons.value) {
    const key = seasonStorageKey(group.number);
    const stored = sessionStorage.getItem(key);
    if (stored === "1") {
      next[key] = true;
    } else if (stored === "0") {
      next[key] = false;
    } else if (openSeasonFromQuery.value && group.number === 1) {
      next[key] = true;
    } else {
      next[key] = false;
    }
  }
  seasonOpen.value = next;
}

function toggleSeason(group: Season) {
  const key = seasonStorageKey(group.number);
  const next = !seasonOpen.value[key];
  seasonOpen.value = { ...seasonOpen.value, [key]: next };
  sessionStorage.setItem(key, next ? "1" : "0");
}

async function loadLibraryFolders() {
  if (!media.value) {
    return;
  }
  try {
    const body = await api<{ items: LibraryChoice[] }>(`/api/v1/media/${media.value.id}/library-folders`);
    libraryFolders.value = body.items;
  } catch {
    libraryFolders.value = [];
  }
}

async function associateFolder(path: string) {
  if (!media.value) {
    return;
  }
  error.value = "";
  try {
    const result = await api<{ attached: number; title: string; path: string }>(
      `/api/v1/media/${media.value.id}/library-folder`,
      { method: "POST", body: JSON.stringify({ path }) },
    );
    message.value = `${result.attached} fichier(s) rattaché(s) à « ${result.title} ». Rien n'est remis en téléchargement.`;
    showFolders.value = false;
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Association impossible.");
  }
}

async function loadJellyfin() {
  if (!media.value || media.value.type !== "series") {
    jellyfin.value = null;
    return;
  }
  jellyfinFailed.value = false;
  try {
    jellyfin.value = await api<JellyfinDiag>(`/api/v1/media/${media.value.id}/jellyfin`);
  } catch (err) {
    jellyfinFailed.value = true;
    jellyfin.value = {
      configured: true,
      series_id: null,
      series_name: null,
      synced_at: null,
      detail: userFacingApiError(err, "Diagnostic Jellyfin indisponible."),
      episodes: [],
      unmatched: [],
    };
  }
}

async function resyncJellyfin() {
  if (!media.value) {
    return;
  }
  error.value = "";
  jellyfinFailed.value = false;
  try {
    jellyfin.value = await api<JellyfinDiag>(`/api/v1/media/${media.value.id}/jellyfin/sync`, { method: "POST" });
    message.value = "Synchro Jellyfin terminée.";
    await reload();
  } catch (err) {
    const detail = userFacingApiError(err, "Resynchronisation impossible.");
    jellyfinFailed.value = true;
    error.value = detail;
    jellyfin.value = {
      configured: jellyfin.value?.configured ?? true,
      series_id: jellyfin.value?.series_id ?? null,
      series_ids: jellyfin.value?.series_ids ?? [],
      series_name: jellyfin.value?.series_name ?? null,
      manual_series_id: jellyfin.value?.manual_series_id ?? null,
      synced_at: jellyfin.value?.synced_at ?? null,
      detail,
      summary: jellyfin.value?.summary ?? null,
      episodes: jellyfin.value?.episodes ?? [],
      unmatched: jellyfin.value?.unmatched ?? [],
    };
  }
}

async function searchJellyfinSeries() {
  if (!media.value) {
    return;
  }
  jellyfinLinkError.value = "";
  jellyfinHits.value = [];
  try {
    const body = await api<{ items: JellyfinSeriesHit[] }>(
      `/api/v1/media/${media.value.id}/jellyfin/series?q=${encodeURIComponent(jellyfinQuery.value.trim())}`,
    );
    jellyfinHits.value = body.items;
    if (!body.items.length) {
      jellyfinLinkError.value = "Aucune série Jellyfin pour ce nom.";
    }
  } catch (err) {
    jellyfinLinkError.value = userFacingApiError(err, "Recherche impossible.");
  }
}

async function linkJellyfinSeries(seriesId: string) {
  if (!media.value) {
    return;
  }
  jellyfinLinkError.value = "";
  try {
    await api(`/api/v1/media/${media.value.id}/jellyfin/link`, {
      method: "PUT",
      body: JSON.stringify({ series_id: seriesId }),
    });
    jellyfinHits.value = [];
    await loadJellyfin();
  } catch (err) {
    jellyfinLinkError.value = userFacingApiError(err, "Liaison impossible.");
  }
}

async function unlinkJellyfinSeries() {
  if (!media.value) {
    return;
  }
  jellyfinLinkError.value = "";
  try {
    await api(`/api/v1/media/${media.value.id}/jellyfin/link`, { method: "DELETE" });
    await loadJellyfin();
  } catch (err) {
    jellyfinLinkError.value = userFacingApiError(err, "Suppression du lien impossible.");
  }
}

function watchLabel(row: Episode): string {
  if (!row.watched) {
    return "Non vu";
  }
  return row.watch_source === "manual" ? "Vu (manuel)" : "Vu";
}

const manualOpen = computed(() => {
  if (!media.value) {
    return true;
  }
  if (!media.value.auto_download || media.value.on_hold) {
    return true;
  }
  return media.value.poll.last_result === "nothing" || media.value.poll.last_result === "error";
});

const seasonCount = computed(() => seasons.value.filter((group) => group.number != null && group.number > 0).length);

const seasonNumbers = computed(() =>
  seasons.value
    .map((group) => group.number)
    .filter((value): value is number => value != null && value > 0)
    .sort((left, right) => left - right),
);

const filmObtained = computed(() => media.value?.type === "film" && media.value.poll.reason === "film_obtained");

const showHiveLocalCatalogueFilm = computed(() => {
  if (!hiveEnabled.value || !media.value || media.value.type !== "film" || media.value.tmdb_id == null) {
    return false;
  }
  const item = filmItem.value;
  if (item?.downloaded) {
    return false;
  }
  const fileStatus = item?.file_status ?? media.value.file_status;
  return fileStatus !== "present";
});

const heroBackdropStyle = computed((): Record<string, string> | null => {
  const url = tmdbBackdropSrc(media.value?.backdrop_url) ?? tmdbPosterSrc(media.value?.poster_url);
  if (!url) {
    return null;
  }
  return { backgroundImage: `url("${url}")` };
});

const typeYearLine = computed(() => {
  const row = media.value;
  if (!row) {
    return "";
  }
  const kind = row.type === "series" ? "Série" : "Film";
  return row.year ? `${kind} · ${row.year}` : kind;
});

const tmdbHeroRatingScore = computed(() => tmdbRatingLabel(media.value?.tmdb_vote_average ?? null));
const tmdbHeroVotePhrase = computed(() => tmdbVoteCountPhrase(media.value?.tmdb_vote_count ?? null));

const filmFileAbsent = computed(
  () => Boolean(media.value?.file_absent) || Boolean(filmItem.value?.file_absent),
);

const ficheHasFiles = computed(() => {
  if (!media.value) {
    return false;
  }
  if (media.value.type === "film") {
    return media.value.on_disk === true;
  }
  for (const group of seasons.value) {
    if (group.file_status_counts && group.file_status_counts.present > 0) {
      return true;
    }
    for (const row of group.episodes) {
      const status = row.file_status || "absent";
      if (status === "present" || row.downloaded) {
        return true;
      }
    }
  }
  return false;
});

const filmFilePresent = computed(
  () =>
    filmItem.value?.file_status === "present" ||
    Boolean(media.value?.on_disk && media.value?.file_status === "present"),
);

const showHiveFicheBlock = computed(() => {
  if (!hiveEnabled.value || !media.value) {
    return false;
  }
  if (!media.value.followed && !ficheHasFiles.value) {
    return false;
  }
  if (media.value.type === "film") {
    return Boolean(primaryItemId.value && filmFilePresent.value);
  }
  return true;
});

const unfollowedMoreSummary = computed(() => {
  const kind = media.value?.type === "series" ? "la série" : "le film";
  return `Garder ${kind}, sources, supprimer la fiche`;
});

const filmTrashEntryId = computed(() => {
  if (!filmItem.value?.trash_entry_id) {
    return null;
  }
  return filmItem.value.trash_entry_id;
});


async function restoreTrashEntry(entryId: number) {
  error.value = "";
  message.value = "";
  try {
    await api(`/api/v1/trash/${entryId}/restore`, { method: "POST" });
    message.value = "Fichier restauré.";
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Restauration impossible.");
  }
}

async function redownloadItem(itemId: number) {
  error.value = "";
  message.value = "";
  try {
    const result = await api<{ message: string }>(`/api/v1/items/${itemId}/redownload`, { method: "POST" });
    message.value = result.message;
    await reload();
  } catch (err) {
    error.value = userFacingApiError(err, "Retéléchargement refusé.");
  }
}

async function restoreFilm() {
  if (filmTrashEntryId.value) {
    await restoreTrashEntry(filmTrashEntryId.value);
  }
}

async function redownloadFilm() {
  if (filmItem.value) {
    await redownloadItem(filmItem.value.id);
  }
}

const defaultProfile = computed(() => qualityProfiles.value.find((row) => row.is_default) ?? null);

const defaultProfileName = computed(() => defaultProfile.value?.name || "défaut");

const principalProfileId = computed((): number | null => {
  const pinned = prefs.value.profileId.trim();
  if (pinned) {
    const parsed = Number(pinned);
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
  }
  return defaultProfile.value?.id ?? null;
});

const profileById = computed(() => new Map(qualityProfiles.value.map((row) => [row.id, row])));

const usesGlobalFallbackChain = computed(() => prefs.value.fallbackMode === "global");

const mediaFallbackIdsForPreview = computed((): number[] | null =>
  usesGlobalFallbackChain.value ? null : prefs.value.customFallbackIds,
);

const effectiveChainIds = computed(() =>
  resolveEffectiveChainIds(
    principalProfileId.value,
    globalFallbackOrderIds.value,
    globalAllowFallback.value,
    mediaFallbackIdsForPreview.value,
  ),
);

const effectiveChain = computed(() =>
  describeEffectiveChain(effectiveChainIds.value, profileById.value, {
    usesGlobal: usesGlobalFallbackChain.value,
    globalAllowFallback: globalAllowFallback.value,
    globalOrder: globalFallbackOrderIds.value,
    principalId: principalProfileId.value,
  }),
);

const globalFallbackLabel = computed(() => (globalAllowFallback.value ? "Oui" : "Non"));

const selectableFallbackProfiles = computed(() =>
  qualityProfiles.value.filter((row) => row.id !== principalProfileId.value),
);

const orderedCustomFallbackProfiles = computed(() =>
  prefs.value.customFallbackIds
    .map((id) => profileById.value.get(id))
    .filter((row): row is QualityProfileOption => row != null),
);

function isCustomFallbackChecked(profileId: number): boolean {
  return prefs.value.customFallbackIds.includes(profileId);
}

function onCustomFallbackToggle(profileId: number, event: Event) {
  const checked = (event.target as HTMLInputElement).checked;
  prefs.value.customFallbackIds = toggleCustomFallbackSelection(
    prefs.value.customFallbackIds,
    profileId,
    principalProfileId.value,
    checked,
  );
}

function shiftCustomFallback(profileId: number, delta: number) {
  prefs.value.customFallbackIds = moveCustomFallback(prefs.value.customFallbackIds, profileId, delta);
}

function revertFallbackToGlobal() {
  prefs.value.fallbackMode = "global";
  prefs.value.customFallbackIds = [];
}

function profileOptionLabel(profile: QualityProfileOption): string {
  return `${profile.name} — ${qualityProfileSummary(profile)}`;
}

const sourceSettingSummary = computed(() => {
  if (!media.value) {
    return "";
  }
  const profileId = prefs.value.profileId.trim();
  if (profileId) {
    const profile = qualityProfiles.value.find((row) => String(row.id) === profileId);
    return profile?.name || "Profil personnalisé";
  }
  return `Profil par défaut (${defaultProfileName.value})`;
});

const conservationSettingSummary = computed(() => {
  if (keepMedia.value) {
    return "Garder toute la fiche";
  }
  const days = autoDays.value.trim();
  if (days) {
    return `Délai ${days} jours`;
  }
  return "Réglage global";
});

const identificationSettingSummary = computed(() => {
  const id = media.value?.tmdb_id;
  return id ? `TMDB ${id}` : "Non renseigné";
});

const progressPercent = computed(() => {
  const row = media.value;
  if (!row || !row.progress.total) {
    return 0;
  }
  return Math.min(100, Math.round((row.progress.watched / row.progress.total) * 1000) / 10);
});

const ficheReglagesSummary = computed(() => {
  if (!media.value) {
    return "";
  }
  const parts = [
    sourceSettingSummary.value,
    `conservation : ${conservationSettingSummary.value}`,
    identificationSettingSummary.value,
    showFolders.value ? "dossier en cours" : "aucun dossier associé",
    jellyfin.value?.synced_at ? "Jellyfin synchronisé" : "Jellyfin",
    "maintenance OK",
  ];
  return parts.join(" · ");
});

const pollVerifLine = computed(() => {
  const row = media.value;
  if (!row) {
    return { last: "—", next: "—" };
  }
  return {
    last: formatParis(row.poll.last_check_at) || "—",
    next: formatParis(row.poll.next_check_at) || "—",
  };
});

const suiviSummaryLine = computed(() => {
  const row = media.value;
  if (!row) {
    return "";
  }
  if (row.type === "film" && row.film_not_yet_available && row.film_status_line) {
    return row.film_status_line;
  }
  const file = fileStatusLabel(row.file_status || "absent", row.type === "film");
  return `Fichier ${file}`;
});

function episodeStatusText(episodeRow: Episode): string {
  if (episodeRow.state === "ambiguous") {
    return "À classer";
  }
  if (episodeRow.download_busy) {
    return "Téléchargement en cours";
  }
  if (episodeRow.source_available && !episodeRow.downloaded && !episodeRow.download_active) {
    return "Prêt, pas téléchargé";
  }
  const status = episodeRow.file_status || "absent";
  if (status === "present" || episodeRow.downloaded) {
    return fileStatusLabel(status, true);
  }
  if (episodeRow.air_date) {
    const today = new Date().toISOString().slice(0, 10);
    if (episodeRow.air_date > today) {
      return `À sortir le ${formatDate(episodeRow.air_date)}`;
    }
  }
  return fileStatusLabel(status, true);
}

function episodeStatusIconName(episodeRow: Episode): string {
  if (episodeRow.download_busy) {
    return "progress";
  }
  if (episodeRow.source_available && !episodeRow.downloaded && !episodeRow.download_active) {
    return "missing";
  }
  return fileStatusIcon(episodeRow.file_status || "absent");
}

</script>

<template>
  <section
    v-if="media"
    class="stack fiche-page fiche-page--a"
    :class="{
      'fiche-page--series': media.type === 'series',
      'fiche-page--unfollowed': !media.followed,
    }"
  >
    <article class="hero panel fiche-hero fiche-hero--a">
      <div
        v-if="heroBackdropStyle"
        class="fiche-hero-backdrop"
        :style="heroBackdropStyle"
        aria-hidden="true"
      />
      <div class="fiche-hero-scrim" aria-hidden="true" />
      <div class="fiche-hero-top">
        <TmdbPosterImg
          v-if="media.poster_url"
          class="poster fiche-poster"
          :url="media.poster_url"
          :alt="media.title"
        />
        <div v-else class="poster fiche-poster fiche-poster-placeholder">Affiche</div>
        <div class="fiche-hero-id">
          <h1 class="fiche-hero-title">
            {{ media.title }}<span v-if="media.year" class="fiche-hero-title-year"> ({{ media.year }})</span>
          </h1>
          <p v-if="media.type === 'series'" class="meta hint fiche-hero-meta">
            {{ typeYearLine }}
            · {{ seriesStatus[media.series_status || "unknown"] || media.series_status }}
            <template v-if="seasonCount"> · {{ seasonCount }} saison{{ seasonCount > 1 ? "s" : "" }}</template>
          </p>
          <p v-else class="meta hint fiche-hero-meta">{{ typeYearLine }}</p>
          <p v-if="tmdbHeroRatingScore" class="meta hint fiche-hero-tmdb-rating">
            <span class="tmdb-rating-star" aria-hidden="true">★</span>
            TMDB {{ tmdbHeroRatingScore }}/10
            <template v-if="tmdbHeroVotePhrase"> · {{ tmdbHeroVotePhrase }}</template>
          </p>
          <p v-if="!media.followed && media.tmdb_url" class="fiche-hero-links hint">
            <a class="fiche-ext-link" :href="media.tmdb_url" target="_blank" rel="noopener noreferrer">TMDB</a>
          </p>
          <p class="fiche-hero-badges">
            <span v-if="media.followed" class="pill ok"><StatusIcon name="followed" label="Suivi" /> Suivi</span>
            <span v-if="media.film_not_yet_available" class="pill warn">
              <StatusIcon name="waiting" label="Pas encore disponible" /> Pas encore disponible
            </span>
            <span v-else-if="media.type === 'film' && media.file_status === 'present'" class="pill info">
              <StatusIcon :name="fileStatusIcon(media.file_status)" label="Fichier" /> Fichier présent
            </span>
            <span
              v-if="hiveEnabled && media.type === 'film' && hiveShare && hiveShare.shared && !hiveShare.exclude_all && filmFilePresent"
              class="pill info hive-hero-share-pill"
            >
              Partage actif
            </span>
          </p>
          <p
            v-if="media.followed && media.type === 'series'"
            class="progress fiche-progress fiche-series-hero-progress"
            aria-label="Progression du visionnage"
          >
            <span>{{ media.progress.watched }} vu sur {{ media.progress.total }}</span>
            <span class="bar fiche-progress-bar" aria-hidden="true"><i :style="{ width: `${progressPercent}%` }" /></span>
            <span v-if="media.next_episode" class="hint">
              Prochain :
              {{ slot(media.next_episode.season, media.next_episode.episode) }}
              <template v-if="media.next_episode.air_date">, le {{ formatDate(media.next_episode.air_date) }}</template>
            </span>
          </p>
          <p v-if="media.type === 'series' && media.overview" class="overview fiche-overview hint fiche-series-overview">
            {{ media.overview }}
          </p>
        </div>
      </div>
      <div class="fiche-hero-stream">
        <p v-if="media.type !== 'series' && media.overview" class="overview fiche-overview hint">{{ media.overview }}</p>
        <FilmReleaseDatesBlock
          v-if="media.type === 'film' && media.tmdb_release_by_region"
          :release-by-region="media.tmdb_release_by_region"
          :catalog-region="catalogRegion"
          :search-from="media.next_air_date"
          :availability-source="media.film_availability_source"
        />
        <template v-if="!media.followed">
          <p class="fiche-primary-row fiche-actions">
            <button
              type="button"
              class="primary fiche-primary-action"
              data-testid="fiche-follow-again"
              @click="followAgain"
            >
              Suivre à nouveau
            </button>
            <button
              v-if="media.tmdb_trailer_youtube_key"
              type="button"
              class="fiche-trailer-btn"
              data-testid="fiche-trailer-open"
              @click="heroTrailerOpen = true"
            >
              <StatusIcon name="play" label="Bande-annonce" />
              Bande-annonce
            </button>
          </p>
        </template>
        <template v-else-if="media.type !== 'series'">
          <FicheStateActions
            section="actions"
            compact
            :media-type="media.type"
            :tmdb-url="media.tmdb_url"
            :jellyfin-web-url="media.jellyfin_web_url"
            :tmdb-trailer-youtube-key="media.tmdb_trailer_youtube_key"
            :tmdb-trailer-kind="media.tmdb_trailer_kind"
            :tmdb-trailer-language="media.tmdb_trailer_language"
            :followed="media.followed"
            :hidden="Boolean(media.hidden_at)"
            :on-hold="media.on_hold"
            :auto-download="media.auto_download"
            :poll="media.poll"
            :progress-watched="media.progress.watched"
            :progress-total="media.progress.total"
            :available-not-downloaded="media.available_not_downloaded"
            :next-episode="media.next_episode"
            :file-status="media.file_status || 'absent'"
            :film-file-absent="filmFileAbsent"
            :film-obtained="filmObtained"
            :film-not-yet-available="Boolean(media.film_not_yet_available)"
            :film-status-line="media.film_status_line"
            :checking="checking"
            :primary-trash-entry-id="filmTrashEntryId"
            :series-watch-menu="false"
            @check="checkNow"
            @pause="setPause"
            @auto="setAuto"
            @restore="restoreFilm"
            @redownload="redownloadFilm"
            @hide="hideFiche"
            @unfollow="unfollow"
            @follow-again="followAgain"
            @watch="markWatched"
          />
        </template>
      </div>
    </article>

    <article
      v-if="!media.followed"
      class="panel fiche-fold fiche-unfollowed-more"
      data-testid="fiche-unfollowed-more"
    >
      <div class="fiche-unfollowed-more-head">
        <StatusIcon name="waiting" label="Plus d'actions" />
        <span class="fold-title">
          <span class="label">Plus d'actions</span>
          <span class="summary hint">{{ unfollowedMoreSummary }}</span>
        </span>
        <LineMoreActions trigger-variant="fiche" menu-aria-label="Plus d'actions sur la fiche">
          <button type="button" class="action-menu-item" role="menuitem" @click="setMediaKeep(!media.keep)">
            {{ media.keep ? "Ne plus garder" : media.type === "series" ? "Garder la série" : "Garder le film" }}
          </button>
          <button type="button" class="action-menu-item" role="menuitem" @click="reglagesOpen = true">
            Réglages et sources
          </button>
          <button
            v-if="media.tmdb_url"
            type="button"
            class="action-menu-item"
            role="menuitem"
            @click="openExternalTab(media.tmdb_url!)"
          >
            Ouvrir TMDB
          </button>
          <button type="button" class="action-menu-item" role="menuitem" @click="reglagesOpen = true">
            Changer l'identifiant TMDB
          </button>
          <template v-if="!media.hidden_at">
            <hr class="action-menu-separator" role="none" />
            <FicheHideMenuEntry @hide="hideFiche" />
          </template>
          <hr class="action-menu-separator" role="none" />
          <button
            v-if="ficheHasFiles"
            type="button"
            class="action-menu-item danger"
            role="menuitem"
            @click="deleteFiles('media', {})"
          >
            <StatusIcon name="trash" label="Supprimer" />
            {{ media.type === "series" ? "Supprimer tous les fichiers" : "Supprimer le film" }}
          </button>
          <button type="button" class="action-menu-item danger" role="menuitem" @click="ficheDelete = true">
            <StatusIcon name="trash" label="Supprimer la fiche" />
            Supprimer la fiche…
          </button>
        </LineMoreActions>
      </div>
    </article>

    <HiveSeriesShareSummary
      v-if="showHiveFicheBlock && media.type === 'series' && !media.followed"
      layout="card"
      :hive-enabled="hiveEnabled"
      :episode-states="hiveEpisodeShares"
      :media-id="media.id"
      :media-requests="hiveSeriesRequests"
      :open-signal="hiveEmpreintesSignal"
      @changed="onHiveSeriesShareChanged"
    />

    <div v-if="media.followed && media.type === 'series'" class="fiche-series-layout stack">
      <article class="panel fiche-suivi-card fiche-layout-b" aria-label="Suivi">
        <header class="fiche-suivi-card-head">
          <StatusIcon name="clock" label="Suivi" />
          <strong>Suivi</strong>
        </header>
        <p class="progress fiche-progress" aria-label="Progression du visionnage">
          <span>{{ media.progress.watched }} vu sur {{ media.progress.total }}</span>
          <span class="bar fiche-progress-bar" aria-hidden="true"><i :style="{ width: `${progressPercent}%` }" /></span>
          <span v-if="media.next_episode" class="hint">
            Prochain :
            {{ slot(media.next_episode.season, media.next_episode.episode) }}
            <template v-if="media.next_episode.air_date">, le {{ formatDate(media.next_episode.air_date) }}</template>
          </span>
        </p>
        <p class="hint fiche-suivi-poll-lines">
          Vérifié {{ pollVerifLine.last }}<br />
          Prochaine {{ pollVerifLine.next }}
        </p>
        <FicheStateActions
          section="actions"
          compact
          :media-type="media.type"
          :tmdb-url="media.tmdb_url"
          :jellyfin-web-url="media.jellyfin_web_url"
          :tmdb-trailer-youtube-key="media.tmdb_trailer_youtube_key"
          :tmdb-trailer-kind="media.tmdb_trailer_kind"
          :tmdb-trailer-language="media.tmdb_trailer_language"
          :followed="media.followed"
          :hidden="Boolean(media.hidden_at)"
          :on-hold="media.on_hold"
          :auto-download="media.auto_download"
          :poll="media.poll"
          :progress-watched="media.progress.watched"
          :progress-total="media.progress.total"
          :available-not-downloaded="media.available_not_downloaded"
          :next-episode="media.next_episode"
          :file-status="media.file_status || 'absent'"
          :film-file-absent="filmFileAbsent"
          :film-obtained="filmObtained"
          :film-not-yet-available="Boolean(media.film_not_yet_available)"
          :film-status-line="media.film_status_line"
          :checking="checking"
          :primary-trash-entry-id="filmTrashEntryId"
          :series-watch-menu="true"
          @check="checkNow"
          @pause="setPause"
          @auto="setAuto"
          @restore="restoreFilm"
          @redownload="redownloadFilm"
          @unfollow="unfollow"
          @follow-again="followAgain"
          @watch="markWatched"
        />
        <div class="hero-toggles fiche-hero-toggles">
          <label class="toggle switch-line">
            <span>Garder la série</span>
            <span class="switch">
              <input type="checkbox" :checked="media.keep" @change="setMediaKeep(checkedFrom($event))" />
              <span class="switch-ui" aria-hidden="true"></span>
            </span>
          </label>
          <label v-if="media.followed" class="toggle switch-line">
            <span>Téléchargement automatique</span>
            <span class="switch">
              <input type="checkbox" :checked="media.auto_download" @change="setAuto(checkedFrom($event))" />
              <span class="switch-ui" aria-hidden="true"></span>
            </span>
          </label>
        </div>
        <div class="fiche-verif-line">
          <button type="button" class="link-button" @click="suiviOpen = !suiviOpen">Détails ▾</button>
        </div>
        <details v-if="suiviOpen" class="fiche-suivi-inline-details" open>
          <summary class="visually-hidden">État détaillé du suivi</summary>
          <FicheStateActions
            section="etat"
            :media-type="media.type"
            :followed="media.followed"
            :hidden="Boolean(media.hidden_at)"
            :on-hold="media.on_hold"
            :auto-download="media.auto_download"
            :poll="media.poll"
            :progress-watched="media.progress.watched"
            :progress-total="media.progress.total"
            :available-not-downloaded="media.available_not_downloaded"
            :next-episode="media.next_episode"
            :file-status="media.file_status || 'absent'"
            :film-file-absent="filmFileAbsent"
            :film-obtained="filmObtained"
            :film-not-yet-available="Boolean(media.film_not_yet_available)"
            :film-status-line="media.film_status_line"
            :checking="checking"
            :primary-trash-entry-id="filmTrashEntryId"
            :series-watch-menu="false"
          />
        </details>
      </article>

      <div v-if="showHiveFicheBlock" class="fiche-layout-b">
        <HiveSeriesShareSummary
          layout="card"
          :hive-enabled="hiveEnabled"
          :episode-states="hiveEpisodeShares"
          :media-id="media.id"
          :media-requests="hiveSeriesRequests"
          :open-signal="hiveEmpreintesSignal"
          @changed="onHiveSeriesShareChanged"
        />
      </div>

      <div class="fiche-series-desktop stack fiche-layout-a">
        <article class="panel fiche-suivi-card fiche-desktop-suivi-hint" aria-label="Suivi">
          <header class="fiche-suivi-card-head">
            <StatusIcon name="clock" label="Suivi" />
            <strong>Suivi</strong>
          </header>
          <p class="hint fiche-suivi-poll-lines">
            Vérifié {{ pollVerifLine.last }}<br />
            Prochaine {{ pollVerifLine.next }}
          </p>
        </article>
        <div class="panel fiche-action-bar">
          <FicheStateActions
            section="actions"
            compact
            :media-type="media.type"
            :tmdb-url="media.tmdb_url"
            :jellyfin-web-url="media.jellyfin_web_url"
            :tmdb-trailer-youtube-key="media.tmdb_trailer_youtube_key"
            :tmdb-trailer-kind="media.tmdb_trailer_kind"
            :tmdb-trailer-language="media.tmdb_trailer_language"
            :followed="media.followed"
            :hidden="Boolean(media.hidden_at)"
            :on-hold="media.on_hold"
            :auto-download="media.auto_download"
            :poll="media.poll"
            :progress-watched="media.progress.watched"
            :progress-total="media.progress.total"
            :available-not-downloaded="media.available_not_downloaded"
            :next-episode="media.next_episode"
            :file-status="media.file_status || 'absent'"
            :film-file-absent="filmFileAbsent"
            :film-obtained="filmObtained"
            :film-not-yet-available="Boolean(media.film_not_yet_available)"
            :film-status-line="media.film_status_line"
            :checking="checking"
            :primary-trash-entry-id="filmTrashEntryId"
            :series-watch-menu="true"
            @check="checkNow"
            @pause="setPause"
            @auto="setAuto"
            @restore="restoreFilm"
            @redownload="redownloadFilm"
            @hide="hideFiche"
            @unfollow="unfollow"
            @follow-again="followAgain"
            @watch="markWatched"
          />
          <div class="hero-toggles fiche-hero-toggles">
            <label class="toggle switch-line">
              <span>Garder la série</span>
              <span class="switch">
                <input type="checkbox" :checked="media.keep" @change="setMediaKeep(checkedFrom($event))" />
                <span class="switch-ui" aria-hidden="true"></span>
              </span>
            </label>
            <label v-if="media.followed" class="toggle switch-line">
              <span>Téléchargement automatique</span>
              <span class="switch">
                <input type="checkbox" :checked="media.auto_download" @change="setAuto(checkedFrom($event))" />
                <span class="switch-ui" aria-hidden="true"></span>
              </span>
            </label>
          </div>
        </div>
        <HiveSeriesShareSummary
          v-if="showHiveFicheBlock"
          layout="card"
          :hive-enabled="hiveEnabled"
          :episode-states="hiveEpisodeShares"
          :media-id="media.id"
          :media-requests="hiveSeriesRequests"
          :open-signal="hiveEmpreintesSignal"
          @changed="onHiveSeriesShareChanged"
        />
        <div class="fiche-verif-line">
          <button type="button" class="link-button" @click="suiviOpen = !suiviOpen">Détails ▾</button>
        </div>
        <details v-if="suiviOpen" class="panel fiche-fold fiche-fold--suivi fiche-series-etat-panel" open>
          <summary class="visually-hidden">État du suivi</summary>
          <div class="fold-body">
            <FicheStateActions
              section="etat"
              :media-type="media.type"
              :followed="media.followed"
              :on-hold="media.on_hold"
              :auto-download="media.auto_download"
              :poll="media.poll"
              :progress-watched="media.progress.watched"
              :progress-total="media.progress.total"
              :available-not-downloaded="media.available_not_downloaded"
              :next-episode="media.next_episode"
              :file-status="media.file_status || 'absent'"
              :film-file-absent="filmFileAbsent"
              :film-obtained="filmObtained"
              :film-not-yet-available="Boolean(media.film_not_yet_available)"
              :film-status-line="media.film_status_line"
              :checking="checking"
              :primary-trash-entry-id="filmTrashEntryId"
              :series-watch-menu="false"
            />
          </div>
        </details>
      </div>
    </div>

    <div v-if="media.followed" class="fiche-status-row">
    <details
      v-if="media.type !== 'series'"
      class="panel fiche-fold fiche-fold--suivi"
      :open="suiviOpen || undefined"
    >
      <summary class="fold-head">
        <StatusIcon name="clock" label="Suivi" />
        <span class="fold-title">
          <span class="label">Suivi</span>
          <span class="summary hint">
            {{ suiviSummaryLine }}<br />
            Vérifié {{ pollVerifLine.last }}<br />
            Prochaine {{ pollVerifLine.next }}
          </span>
        </span>
        <span class="open-txt">Détails</span>
        <span class="chev" aria-hidden="true">▾</span>
      </summary>
      <div class="fold-body">
        <FicheStateActions
          section="etat"
          :media-type="media.type"
          :followed="media.followed"
          :hidden="Boolean(media.hidden_at)"
          :on-hold="media.on_hold"
          :auto-download="media.auto_download"
          :poll="media.poll"
          :progress-watched="media.progress.watched"
          :progress-total="media.progress.total"
          :available-not-downloaded="media.available_not_downloaded"
          :next-episode="media.next_episode"
          :file-status="media.file_status || 'absent'"
          :film-file-absent="filmFileAbsent"
          :film-obtained="filmObtained"
          :film-not-yet-available="Boolean(media.film_not_yet_available)"
          :film-status-line="media.film_status_line"
          :checking="checking"
          :primary-trash-entry-id="filmTrashEntryId"
          :series-watch-menu="false"
        />
      </div>
    </details>

    <HiveMediaRequestCard
      v-if="
        hiveEnabled &&
        media.followed &&
        media.type !== 'series' &&
        primaryItemId &&
        hiveItemRequest &&
        (hiveItemRequest.announced || hiveItemRequest.promptAvailable || hiveItemRequest.retracted) &&
        !(media.type === 'film' && filmItem?.downloaded)
      "
      class="fiche-fold fiche-fold--hive"
      :media-item-id="primaryItemId"
      :announced="hiveItemRequest.announced"
      :prompt-available="hiveItemRequest.promptAvailable"
      :retracted="hiveItemRequest.retracted"
      @changed="refreshHiveItemRequest"
    />
    </div>

    <HiveMediaShareRow
      v-if="showHiveFicheBlock && media.type === 'film' && primaryItemId && filmFilePresent"
      :state="hiveShare"
      :hive-enabled="hiveEnabled"
      :media-item-id="primaryItemId"
      :media-id="media.id"
      :open-signal="hiveEmpreintesSignal"
      :request-error="hiveSharePolling.shareRequestError.value"
      :poll-exhausted="hiveSharePolling.pollExhausted.value"
      :poll-busy="hiveSharePolling.pollBusy.value"
      @refresh="hiveSharePolling.manualRefresh"
      @changed="onHiveShareMenuChanged"
    />

    <p
      v-if="hideJustDone && media.hidden_at"
      class="profile-repl-banner"
      role="status"
      data-testid="fiche-hidden-undo-banner"
    >
      Fiche masquée.
      <button type="button" class="linkish" @click="undoHideFiche">Annuler</button>
    </p>
    <p v-else-if="media.hidden_at" class="profile-repl-banner" role="status" data-testid="fiche-hidden-banner">
      Fiche masquée : absente de Médias, de l'Accueil et des suggestions. Rien n'est supprimé.
      <button type="button" class="linkish" @click="unhideFiche">Réafficher</button>
    </p>

    <p v-if="error" class="error" data-force-status>{{ error }}</p>
    <p v-else-if="message" class="hint" data-force-status>{{ message }}</p>

    <HiveLocalCatalogueBlock
      v-if="showHiveLocalCatalogueFilm"
      media-type="film"
      :tmdb-id="media.tmdb_id!"
      @changed="reload"
    />

    <article v-if="media.type === 'film' && filmItem && !media.film_not_yet_available" class="panel film-file-panel">
      <h2>Film</h2>
      <FilmFileBlock
        :hive-enabled="hiveEnabled"
        :item="filmItem"
        :hive-share-chips="primaryItemId ? hiveChipsForEpisode(primaryItemId) : []"
        :keep="keepMedia"
        :media-id="media.id"
        @mark-watched="(undo) => markWatched({ item_id: filmItem!.id, undo })"
        @set-keep="setMediaKeep"
        @restore="filmItem.trash_entry_id != null && restoreTrashEntry(filmItem.trash_entry_id)"
        @redownload="redownloadItem(filmItem!.id)"
        @force-download="forceDownload({ item_id: filmItem!.id })"
        @delete-file="deleteFiles('episode', { item_id: filmItem!.id })"
        @hive-share-changed="onHiveShareMenuChanged"
        @profile-replacement-changed="reload"
      />
    </article>

    <p
      v-if="media.type === 'series' && hiveEnabled && hiveSeriesCatalogue.error.value"
      class="error"
      role="alert"
      data-testid="hive-series-catalogue-error"
    >
      {{ hiveSeriesCatalogue.error.value }}
    </p>
    <section v-if="media.type === 'series' && (media.followed || ficheHasFiles)" class="fiche-seasons-stack">
      <article class="panel fiche-seasons-intro">
        <h2 class="fiche-seasons-title">
          Saisons et épisodes
          <span class="hint">
            · {{ seasonCount }} saison{{ seasonCount > 1 ? "s" : "" }} · {{ media.progress.watched }} vu sur
            {{ media.progress.total }}
          </span>
        </h2>
      </article>
      <article
        v-for="group in seasons"
        :key="String(group.number)"
        class="panel fiche-season-panel season"
        data-fiche-season
      >
        <div
          class="season-head"
          :class="{
            open: seasonOpen[seasonStorageKey(group.number)],
            closed: !seasonOpen[seasonStorageKey(group.number)],
          }"
        >
          <button
            type="button"
            class="chev-btn"
            :aria-expanded="seasonOpen[seasonStorageKey(group.number)] ? 'true' : 'false'"
            :aria-label="`Afficher ou masquer ${seasonTitle(group)}`"
            @click="toggleSeason(group)"
          >
            <span class="chev" aria-hidden="true">▾</span>
          </button>
          <div class="season-title">
            <strong>{{ seasonTitle(group) }}</strong>
            <div class="counts">
              <span class="pill" :class="watchedTone(group)"
                >{{ watchedCount(group) }} {{ watchedCount(group) === 1 ? "vu" : "vus" }}</span
              >
              <span class="pill" :class="onDiskTone(group)"
                >{{ presentOnDiskCount(group) }}
                {{ presentOnDiskCount(group) === 1 ? "fichier sur disque" : "fichiers sur disque" }}</span
              >
              <span class="pill muted"
                >{{ catalogEpisodeCount(group) }}
                {{ catalogEpisodeCount(group) === 1 ? "épisode au catalogue" : "épisodes au catalogue" }}</span
              >
              <span v-if="missingCatalogSlots(group) > 0" class="pill warn">{{ missingCatalogLabel(group) }}</span>
              <span v-if="readyWithoutFileCount(group) > 0" class="pill warn">{{ readyWithoutFileLabel(group) }}</span>
              <span v-if="absentWithoutSourceCount(group) > 0" class="pill danger">{{
                absentWithoutSourceLabel(group)
              }}</span>
              <span v-if="hiveEnabled && seasonShareLabel(group)" class="pill info hive-season-share">{{
                seasonShareLabel(group)
              }}</span>
            </div>
          </div>
          <div v-if="group.number != null" class="ctrl season-ctrl fiche-ctrl" @click.stop>
            <span class="ctrl-seen" aria-hidden="true"></span>
            <label class="switch-line compact season-keep ctrl-keep">
              <span>Garder</span>
              <span class="switch">
                <input
                  type="checkbox"
                  :checked="group.keep"
                  :disabled="media.keep"
                  @change="group.number != null && setSeasonKeep(group.number, checkedFrom($event))"
                />
                <span class="switch-ui" aria-hidden="true"></span>
              </span>
            </label>
            <LineMoreActions trigger-variant="fiche" menu-aria-label="Plus d'actions sur la saison">
              <HiveBulkShareMenuItems
                v-if="hiveEnabled && group.number != null"
                :media-id="media.id"
                :season="group.number"
                :hive-enabled="hiveEnabled"
                @changed="refreshHiveEpisodeShares"
                @open-empreintes="hiveEmpreintesSignal += 1"
              />
              <button
                type="button"
                role="menuitem"
                @click="group.number != null && markSeasonWatched(group.number)"
              >
                Marquer la saison comme vue
              </button>
              <button
                type="button"
                role="menuitem"
                @click="group.number != null && markSeasonWatched(group.number, true)"
              >
                Marquer la saison comme non vue
              </button>
              <button
                type="button"
                class="danger"
                role="menuitem"
                @click="group.number != null && deleteFiles('season', { season: group.number })"
              >
                <StatusIcon name="trash" label="Supprimer" /> Supprimer la saison
              </button>
              <button
                type="button"
                data-force-season
                role="menuitem"
                @click="group.number != null && forceDownload({ season: group.number })"
              >
                Forcer la saison
              </button>
            </LineMoreActions>
          </div>
        </div>
        <template v-if="seasonOpen[seasonStorageKey(group.number)]">
          <p
            v-if="group.number != null && seasonNotice[seasonStorageKey(group.number)]"
            class="hint success fiche-season-notice"
            data-season-watch-notice
          >
            {{ seasonNotice[seasonStorageKey(group.number)] }}
          </p>
          <ul class="episode-list eps fiche-eps">
            <li v-for="(episodeRow, epIndex) in group.episodes" :key="episodeRow.id" class="episode-row ep fiche-ep">
              <ProfileReplacementBanner
                v-if="episodeRow.profile_replacement"
                :item-id="episodeRow.id"
                :proposal="episodeRow.profile_replacement"
                class="ep-profile-repl"
                @changed="reload"
              />
              <span class="episode-code ep-code">{{ episodeRow.episode == null ? "—" : slot(group.number, episodeRow.episode) }}</span>
              <div class="ep-main">
                <div class="episode-row-head ep-title">
                  <span class="episode-title">{{ episodeRow.title || "Sans titre" }}</span>
                </div>
                <div class="episode-row-meta ep-meta">
                  <span class="episode-status-line icon-line st-ok" :title="episodeStatusText(episodeRow)">
                    <StatusIcon
                      :name="episodeStatusIconName(episodeRow)"
                      :label="episodeStatusText(episodeRow)"
                      :spin="episodeRow.download_busy"
                    />
                    <span>{{ episodeStatusText(episodeRow) }}</span>
                  </span>
                  <span v-if="episodeRow.filename" class="file-name file" :title="episodeRow.filename">{{ episodeRow.filename }}</span>
                  <span v-if="episodeRow.served_profile?.repli" class="hint file-served-profile">
                    Profil utilisé : {{ episodeRow.served_profile.profile_name }} (repli)
                  </span>
                  <span v-if="formatDate(episodeRow.air_date)" class="hint">
                    Diffusion {{ formatDate(episodeRow.air_date) }}
                  </span>
                  <template v-if="hiveEnabled">
                    <span
                      v-for="chip in hiveChipsForEpisode(episodeRow.id)"
                      :key="chip.key"
                      class="hive-ep-chip"
                      :class="[`tone-${chip.tone}`, { 'has-bar': chip.showBar }]"
                    >
                      <span class="chip-label-short">{{ chip.labelShort }}</span>
                      <span class="chip-label-long">{{ chip.labelLong }}</span>
                      <span v-if="chip.showBar && chip.barPct != null" class="hive-ep-chip-bar" aria-hidden="true">
                        <i :style="{ width: `${chip.barPct}%` }" />
                      </span>
                    </span>
                  </template>
                </div>
              </div>
              <div class="ctrl ep-ctrl fiche-ctrl" @click.stop>
                <span class="ctrl-seen" :class="{ 'is-empty': !episodeRow.watched }">
                  <span v-if="episodeRow.watched" class="episode-watched seen icon-line" :title="watchLabel(episodeRow)">
                    <StatusIcon name="watched" :label="watchLabel(episodeRow)" /> vu
                  </span>
                </span>
                <label
                  class="keep-toggle switch-line compact ctrl-keep"
                  :title="episodeKeepEffective(group, episodeRow) ? 'Garder : exclu du nettoyage automatique' : 'Garder cet épisode'"
                >
                  <span>Garder</span>
                  <span class="switch">
                    <input
                      type="checkbox"
                      :checked="episodeRow.keep"
                      :disabled="media.keep || group.keep"
                      @change="setEpisodeKeep(episodeRow.id, checkedFrom($event))"
                    />
                    <span class="switch-ui" aria-hidden="true"></span>
                  </span>
                </label>
                <LineMoreActions trigger-variant="fiche" menu-aria-label="Plus d'actions sur la saison">
                  <button
                    type="button"
                    role="menuitem"
                    @click="markWatched({ item_id: episodeRow.id, undo: episodeRow.watched })"
                  >
                    {{ episodeRow.watched ? 'Marquer comme non vu' : 'Marquer comme vu' }}
                  </button>
                  <button
                    v-if="group.number != null && episodeRow.episode != null"
                    type="button"
                    role="menuitem"
                    @click="markWatched({ until_season: group.number, until_episode: episodeRow.episode })"
                  >
                    Marquer comme vu jusqu'à cet épisode
                  </button>
                  <button
                    v-if="episodeRow.trash_entry_id"
                    type="button"
                    role="menuitem"
                    @click="restoreTrashEntry(episodeRow.trash_entry_id!)"
                  >
                    Restaurer
                  </button>
                  <button
                    v-else-if="episodeRow.file_status === 'absent' && episodeRow.file_absent"
                    type="button"
                    role="menuitem"
                    @click="redownloadItem(episodeRow.id)"
                  >
                    Retélécharger
                  </button>
                  <template v-if="episodeRow.state === 'ambiguous'">
                    <p class="hint episode-classify" role="none">
                      À classer :
                      <input
                        :value="slotDraft[episodeRow.id]?.season || ''"
                        placeholder="S"
                        inputmode="numeric"
                        @input="ensureDraft(episodeRow.id, 'season', ($event.target as HTMLInputElement).value)"
                      />
                      <input
                        :value="slotDraft[episodeRow.id]?.episode || ''"
                        placeholder="E"
                        inputmode="numeric"
                        @input="ensureDraft(episodeRow.id, 'episode', ($event.target as HTMLInputElement).value)"
                      />
                      <button type="button" role="menuitem" @click="assignSlot(episodeRow.id)">Classer</button>
                    </p>
                  </template>
                  <button
                    v-if="episodeRow.source_available && !episodeRow.downloaded && !episodeRow.download_active && manualOpen"
                    type="button"
                    class="primary"
                    role="menuitem"
                    @click="queueEpisode(episodeRow.id)"
                  >
                    Télécharger
                  </button>
                  <HiveContextMenuItems
                    :media-item-id="episodeRow.id"
                    :media-id="media.id"
                    :hive-enabled="hiveEnabled"
                    :versions-to="`/medias/${media.id}?reglages=ouverts`"
                    @changed="reload"
                  />
                  <button
                    v-if="episodeRow.source_available || episodeFileOnDisk(episodeRow) || episodeRow.downloaded"
                    type="button"
                    class="force-download"
                    data-force-download
                    role="menuitem"
                    @click="forceDownload({ item_id: episodeRow.id })"
                  >
                    <span class="force-dl-short">{{ forceRedownloadLabel(true, episodeFileOnDisk(episodeRow)) }}</span>
                    <span class="force-dl-long">{{ forceRedownloadLabel(false, episodeFileOnDisk(episodeRow)) }}</span>
                  </button>
                  <button
                    v-if="episodeRow.downloaded"
                    type="button"
                    class="danger"
                    role="menuitem"
                    @click="deleteFiles('episode', { item_id: episodeRow.id })"
                  >
                    <StatusIcon name="trash" label="Supprimer" /> Supprimer
                  </button>
                </LineMoreActions>
              </div>
              <HiveLocalCatalogueBlock
                v-if="hiveEnabled && media.tmdb_id != null && !episodeRow.downloaded"
                media-type="series"
                :tmdb-id="media.tmdb_id"
                :season="group.number"
                :episode="episodeRow.episode"
                :catalogue-work="hiveSeriesCatalogue.episodeWork(group.number, episodeRow.episode)"
                @changed="() => { void hiveSeriesCatalogue.refresh(); void reload(); }"
              />
            </li>
          </ul>
        </template>
      </article>
    </section>

    <details
      v-if="media.followed || reglagesOpen"
      class="panel fiche-settings-block fiche-fold"
      data-fiche-settings
      :open="reglagesOpen || undefined"
      @toggle="reglagesOpen = ($event.target as HTMLDetailsElement).open"
    >
      <summary class="fold-head">
        <StatusIcon name="gear" label="Réglages" />
        <span class="fold-title">
          <span class="label">Réglages de cette fiche</span>
          <span class="summary hint">{{ ficheReglagesSummary }}</span>
        </span>
        <span class="open-txt">{{ reglagesOpen ? "Masquer" : "Afficher" }}</span>
        <span class="chev" aria-hidden="true">▾</span>
      </summary>
      <div class="fold-body">
        <div class="set-grid">
          <article class="set-card">
            <div class="top">
              <h3>Source</h3>
              <span class="pill muted">{{ sourceSettingSummary }}</span>
            </div>
            <p class="desc hint">Profil principal, repli et ordre des sources pour cette fiche.</p>
            <details class="fiche-setting-details" data-testid="fiche-prefs-details">
              <summary class="fiche-setting-summary"><span>Préférences de suivi</span></summary>
        <form class="fiche-profile-prefs" @submit.prevent="savePrefs">
          <label>
            <span>Profil principal</span>
            <select v-model="prefs.profileId" class="profile-select-wide">
              <option value="">Par défaut — {{ defaultProfileName }}</option>
              <option v-for="profile in qualityProfiles" :key="profile.id" :value="String(profile.id)">
                {{ profileOptionLabel(profile) }}
              </option>
            </select>
            <span class="hint">Choisir un profil existant, ou utiliser le profil par défaut.</span>
          </label>
          <div class="profile-fallback-switchline fiche-profile-switch">
            <span class="field-label">Repli sur cette fiche</span>
            <div class="segment-toggle" role="group" aria-label="Mode de repli sur cette fiche">
              <button
                type="button"
                :aria-pressed="prefs.fallbackMode === 'global'"
                @click="prefs.fallbackMode = 'global'"
              >
                Suivre le réglage global ({{ globalFallbackLabel }})
              </button>
              <button
                type="button"
                :aria-pressed="prefs.fallbackMode === 'custom'"
                @click="prefs.fallbackMode = 'custom'"
              >
                Personnaliser
              </button>
            </div>
          </div>
          <p v-if="prefs.fallbackMode === 'global'" class="hint">
            Les fiches qui suivent le global utilisent « Repli par défaut » et l'ordre global des profils.
          </p>
          <template v-else>
          <div class="profile-fallback-section">
            <p class="hint">Profil principal fixé en tête ; cochez et ordonnez les profils de repli pour cette fiche.</p>
            <ul class="fiche-fallback-pick" aria-label="Profils de repli disponibles">
              <li v-for="profile in selectableFallbackProfiles" :key="profile.id">
                <label class="switch-line">
                  <span>{{ profileOptionLabel(profile) }}</span>
                  <span class="switch switch-small">
                    <input
                      type="checkbox"
                      :checked="isCustomFallbackChecked(profile.id)"
                      @change="onCustomFallbackToggle(profile.id, $event)"
                    />
                    <span class="switch-ui" aria-hidden="true"></span>
                  </span>
                </label>
              </li>
            </ul>
            <div v-if="orderedCustomFallbackProfiles.length" class="profile-fallback-section" aria-label="Ordre de repli">
              <div
                v-for="(profile, index) in orderedCustomFallbackProfiles"
                :key="profile.id"
                class="profile-fallback-row"
              >
                <span class="profile-fallback-rank">{{ index + 2 }}</span>
                <div class="profile-fallback-profile">
                  <strong>{{ profile.name }}</strong>
                  <small class="hint">{{ qualityProfileSummary(profile) }}</small>
                </div>
                <div class="profile-fallback-actions">
                  <button
                    type="button"
                    :disabled="index === 0"
                    :aria-label="`Monter ${profile.name}`"
                    @click="shiftCustomFallback(profile.id, -1)"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    :disabled="index === orderedCustomFallbackProfiles.length - 1"
                    :aria-label="`Descendre ${profile.name}`"
                    @click="shiftCustomFallback(profile.id, 1)"
                  >
                    ↓
                  </button>
                </div>
              </div>
            </div>
            <p class="actions">
              <button type="button" class="ghost" @click="revertFallbackToGlobal">Revenir au réglage global</button>
            </p>
          </div>
          </template>
          <div class="fiche-chain-block">
            <h4 class="fiche-chain-title">Chaîne pour cette fiche</h4>
            <p v-if="prefsContextStatus === 'loading'" class="hint" role="status">Chargement de la chaîne…</p>
            <template v-else-if="prefsContextStatus === 'error'">
              <p class="error" role="alert">{{ prefsContextError }}</p>
              <p class="actions">
                <button type="button" @click="loadPrefsContext">Réessayer</button>
              </p>
            </template>
            <template v-else>
              <ul v-if="effectiveChain.rows.length" class="fiche-chain-list" aria-label="Chaîne de profils">
                <li v-for="(row, index) in effectiveChain.rows" :key="row.profileId">
                  <strong>{{ index + 1 }}. {{ row.name }}</strong>
                  <span class="hint"> · {{ row.role }}</span>
                </li>
              </ul>
              <p v-if="effectiveChain.hint" class="hint">{{ effectiveChain.hint }}</p>
              <RouterLink class="fiche-chain-link" to="/reglages/qualite">
                Modifier les profils de repli globaux
              </RouterLink>
              <p v-if="prefs.fallbackMode === 'global'" class="hint">
                Chaîne effective pour cette fiche tant qu'elle suit le réglage global.
              </p>
            </template>
          </div>
          <p v-if="effectiveChainIds.length <= 1" class="profile-fallback-notice" role="status">
            Repli désactivé pour cette fiche : seul le profil principal est essayé.
          </p>
          <p
            v-if="!downloadSourcesReady && downloadSourcesBlockedMessage"
            class="hint download-sources-blocked"
            role="status"
          >
            {{ downloadSourcesBlockedMessage }}
            <RouterLink to="/reglages/alldebrid">AllDebrid</RouterLink>
            ou
            <RouterLink to="/reglages/hive">Hive</RouterLink>.
          </p>
          <div>
            <span>Ordre des sources</span>
            <SourceRank v-model="prefs.sourceOrder" :visible-ids="availableDownloadSourceIds" />
            <span class="hint">Vide : l'ordre global. Cochez une source pour un ordre propre à cette fiche.</span>
            <p v-if="!prefs.sourceOrder.trim() && globalDownloadSourceOrder" class="hint" role="status">
              Ordre global actuel : {{ globalDownloadSourceOrder }}
            </p>
          </div>
          <label>
            <span>Intervalle propre (minutes)</span>
            <input v-model="prefs.interval" inputmode="numeric" placeholder="Vide : réglage global, 720" />
          </label>
          <p class="hint">Ordre des sources inchangé.</p>
          <button type="submit" class="primary" :disabled="prefsContextStatus !== 'ready'">Enregistrer</button>
        </form>
            </details>
          </article>

          <article class="set-card">
            <div class="top">
              <h3>Conservation</h3>
              <span class="pill muted">{{ conservationSettingSummary }}</span>
            </div>
            <p class="desc hint">Garder la fiche ou appliquer un délai de nettoyage automatique.</p>
            <details class="fiche-setting-details">
              <summary class="fiche-setting-summary"><span>Modifier la conservation</span></summary>
        <form @submit.prevent="saveRetention">
          <label class="switch-line">
            <span>Garder</span>
            <span class="switch">
              <input v-model="keepMedia" type="checkbox" />
              <span class="switch-ui" aria-hidden="true"></span>
            </span>
            <span class="hint">Hors nettoyage automatique pour toute la fiche.</span>
          </label>
          <p v-if="conservationRetentionHint" class="hint">{{ conservationRetentionHint }}</p>
          <label class="label-inline-field">
            <span>Délai propre</span>
            <span class="inline-number-field">
              <input
                v-model="autoDays"
                class="input-compact"
                inputmode="numeric"
                :placeholder="autoDaysPlaceholder"
              />
              <span class="inline-field-suffix">jours</span>
            </span>
          </label>
          <button type="submit" class="primary">Enregistrer</button>
        </form>
            </details>
          </article>

          <article class="set-card">
            <div class="top">
              <h3>Identification</h3>
              <span class="pill muted">{{ identificationSettingSummary }}</span>
            </div>
            <p class="desc hint">Identifiant TMDB et historique des corrections.</p>
            <details class="fiche-setting-details">
              <summary class="fiche-setting-summary"><span>Corriger l'identifiant</span></summary>
        <form @submit.prevent="saveTmdb">
          <label>
            <span>Identifiant TMDB</span>
            <input v-model="tmdbDraft" inputmode="numeric" />
            <span class="hint">
              La dernière écriture gagne. Celle-ci est poussée vers Jellyfin, les métadonnées
              sont relues, puis le dossier prend le tag [tmdbid-N]. Une sauvegarde Jellyfin
              plus récente reprend la main.
            </span>
          </label>
          <p v-if="media.identity_edited_at" class="hint">
            Dernière écriture MediaTracker : {{ formatParis(media.identity_edited_at) }}.
          </p>
          <button type="submit">Corriger et pousser vers Jellyfin</button>
        </form>
        <ul v-if="identity.length" class="entry-list">
          <li v-for="row in identity" :key="row.id">
            <div class="entry-main">
              <span class="entry-copy">
                {{ row.source === "jellyfin" ? "Jellyfin" : "MediaTracker" }}
                · {{ row.old_tmdb ?? "—" }} → {{ row.new_tmdb ?? "—" }}
                <span class="hint">{{ formatParis(row.changed_at) }}</span>
                <span v-if="row.undone_at" class="hint">Annulé</span>
              </span>
              <button v-if="!row.undone_at" type="button" @click="undoIdentity(row.id)">Annuler</button>
            </div>
          </li>
        </ul>
            </details>
          </article>

          <article v-if="media.followed" class="set-card">
            <div class="top">
              <h3>Bibliothèque</h3>
              <span class="pill muted">{{ showFolders ? "association…" : "Dossier non associé" }}</span>
            </div>
            <p class="desc hint">Relie un dossier déjà rangé sur le NAS.</p>
            <div class="acts">
              <button type="button" @click="showFolders = !showFolders">Associer un dossier de la bibliothèque</button>
            </div>
            <ul v-if="showFolders" class="entry-list">
              <li v-for="choice in libraryFolders" :key="choice.path">
                <div class="entry-main">
                  <span class="entry-copy">
                    <strong>{{ choice.name }}</strong>
                    <span class="hint">
                      <template v-if="choice.year">{{ choice.year }} · </template>
                      {{ choice.file_count }} fichier(s)
                    </span>
                  </span>
                </div>
                <div class="entry-actions">
                  <button type="button" class="primary" @click="associateFolder(choice.path)">Associer</button>
                </div>
              </li>
            </ul>
          </article>

          <article v-if="media.type === 'series'" class="set-card">
            <div class="top">
              <h3>Jellyfin</h3>
              <span class="pill ok" v-if="jellyfin?.synced_at">Synchronisé</span>
            </div>
            <p class="desc hint">
              Dernière synchro :
              {{ jellyfin?.synced_at ? formatDateTime(jellyfin.synced_at) : "jamais" }}
            </p>
            <div class="acts">
              <button type="button" class="primary" @click="resyncJellyfin">Resynchroniser</button>
            </div>
            <details class="jellyfin-detail">
              <summary>Détail et lien manuel</summary>
              <div class="jellyfin-bar">
                <span v-for="chip in jellyfinChips" :key="chip.key" class="jellyfin-chip" :class="chip.key">{{ chip.label }}</span>
              </div>
              <label>
                <span>Lier à une série Jellyfin</span>
                <input v-model="jellyfinQuery" placeholder="Nom de la série" @keydown.enter.prevent="searchJellyfinSeries" />
              </label>
              <button type="button" @click="searchJellyfinSeries">Chercher</button>
              <p v-if="jellyfinLinkError" class="error">{{ jellyfinLinkError }}</p>
            </details>
          </article>

          <article class="set-card">
            <div class="top">
              <h3>Détails techniques</h3>
            </div>
            <p class="desc hint">Versions sources, intervalle de vérification, journal.</p>
            <div class="acts">
              <button type="button" @click="showDetails = !showDetails">Versions sources</button>
              <router-link :to="{ path: '/journal', query: { media_id: String(media.id) } }">Voir dans le journal</router-link>
            </div>
            <div v-if="showDetails">
              <ul v-if="media.source_versions?.length" class="hint">
                <li v-for="(version, index) in media.source_versions" :key="index">
                  {{ version.title }}
                  <template v-if="version.quality"> · {{ version.quality }}</template>
                </li>
              </ul>
              <p v-else class="hint">Aucune version source enregistrée.</p>
            </div>
          </article>

          <article class="set-card wide">
            <FicheMaintenance
              :media-id="media.id"
              :media-type="media.type"
              :season-numbers="seasonNumbers"
              :open="false"
              @updated="reload"
            />
          </article>
        </div>
      </div>
    </details>

    <details v-if="media.followed" class="panel fiche-fold fiche-suivi danger-zone">
      <summary class="fold-head">
        <StatusIcon name="trash" label="Suppression" />
        <span class="fold-title">
          <span class="label">Suppression et fin du suivi</span>
          <span class="summary hint">Ne plus suivre, supprimer les fichiers, supprimer la fiche — rien sans confirmation</span>
        </span>
        <span class="chev" aria-hidden="true">▾</span>
      </summary>
      <div class="fold-body fiche-suivi-body">
        <article class="fiche-suivi-option">
          <div class="fiche-suivi-option-copy">
            <h3>Ne plus suivre</h3>
            <p class="hint fiche-suivi-lead">
              Arrête les vérifications et les notifications liées à cette fiche. L'état « vu » et les fichiers restent en
              place, sauf si vous choisissez la corbeille ci-dessous.
            </p>
            <label class="switch-line">
              <span>Mettre aussi les fichiers à la corbeille en arrêtant le suivi</span>
              <span class="switch">
                <input v-model="trashOnUnfollow" type="checkbox" :disabled="!media.followed" />
                <span class="switch-ui" aria-hidden="true"></span>
              </span>
            </label>
            <p v-if="!media.followed" class="hint fiche-suivi-unavail">
              <StatusIcon name="pause" label="Indisponible" />
              Indisponible : la fiche n'est déjà plus suivie. Pour la reprendre : « Suivre à nouveau », en haut.
            </p>
          </div>
          <button type="button" class="danger" :disabled="!media.followed" @click="unfollow">Ne plus suivre</button>
        </article>

        <article class="fiche-suivi-option">
          <div class="fiche-suivi-option-copy">
            <h3>
              Supprimer {{ media.type === "series" ? "tous les fichiers" : "le film" }}
              <span class="badge">corbeille</span>
            </h3>
            <p class="hint fiche-suivi-lead">
              Met les fichiers en corbeille (restaurables). La fiche MediaTracker et l'état « vu » sont conservés.
            </p>
            <p v-if="!ficheHasFiles" class="hint fiche-suivi-unavail">
              <StatusIcon name="pause" label="Indisponible" />
              Aucun fichier présent
            </p>
          </div>
          <button type="button" class="danger" :disabled="!ficheHasFiles" @click="deleteFiles('media', {})">
            Supprimer {{ media.type === "series" ? "tous les fichiers" : "le film" }}
          </button>
        </article>

        <article class="fiche-suivi-option">
          <div class="fiche-suivi-option-copy">
            <h3>Supprimer la fiche</h3>
            <p class="hint fiche-suivi-lead">
              Retire la fiche de MediaTracker. Vous choisirez ensuite si les fichiers restent sur le disque ou passent en
              corbeille.
            </p>
          </div>
          <button type="button" class="danger" @click="ficheDelete = true">Supprimer la fiche…</button>
        </article>
      </div>
    </details>

    <TrailerPlayerDialog
      v-if="media"
      :open="heroTrailerOpen"
      :youtube-key="media.tmdb_trailer_youtube_key || ''"
      :subtitle="heroTrailerSubtitle"
      @close="heroTrailerOpen = false"
    />

    <DeleteFicheDialog
      :open="ficheDelete"
      :count="1"
      :title="media.title"
      @cancel="ficheDelete = false"
      @confirm="removeFiche"
    />

    <TmdbIdChangeDialog
      :open="tmdbChangeOpen"
      :preview="tmdbChangePreview"
      :busy="tmdbChangeBusy"
      @cancel="closeTmdbChangeDialog"
      @confirm="confirmTmdbChange"
    />
  </section>
</template>

<style scoped>
.hive-ep-chip,
.hive-season-share {
  font-size: 0.74rem;
  font-weight: 700;
  color: var(--info);
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.12rem 0.45rem;
  border-radius: 999px;
  border: 1px solid color-mix(in srgb, currentColor 45%, transparent);
  background: color-mix(in srgb, currentColor 14%, transparent);
  line-height: 1.2;
}
.hive-ep-chip.tone-muted {
  color: var(--muted);
}
.hive-ep-chip.tone-danger {
  color: var(--danger);
}
.hive-ep-chip.tone-info,
.hive-ep-chip.tone-honey {
  color: var(--info);
}
.hive-ep-chip.tone-ok {
  color: var(--ok);
}
.hive-ep-chip.tone-warn {
  color: var(--warn);
}
.hive-ep-chip .chip-label-long {
  display: none;
}
.hive-ep-chip-bar {
  display: inline-block;
  width: 2.5rem;
  height: 0.25rem;
  background: rgb(255 255 255 / 12%);
  border-radius: 2px;
  vertical-align: middle;
}
.hive-ep-chip-bar > i {
  display: block;
  height: 100%;
  background: currentColor;
  border-radius: 2px;
  opacity: 0.85;
}
.force-dl-long {
  display: none;
}
@media (min-width: 721px) {
  .hive-ep-chip .chip-label-short {
    display: none;
  }
  .hive-ep-chip .chip-label-long {
    display: inline;
  }
  .force-dl-short {
    display: none;
  }
  .force-dl-long {
    display: inline;
  }
}
</style>

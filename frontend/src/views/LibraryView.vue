<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import MediaQuickView from "../components/MediaQuickView.vue";
import StatusIcon from "../components/StatusIcon.vue";
import TmdbPosterImg from "../components/TmdbPosterImg.vue";
import { useLibraryVerifyAll } from "../composables/useLibraryVerifyAll";
import { useServiceStatus } from "../composables/useServiceStatus";
import {api, userFacingApiError} from "../api";
import { candidateFacts, mediaKind, tmdbPage, tmdbSearch } from "../labels";
import type { QuickTarget } from "../quick";
import { fileSizeLine, formatBytes, jobLine } from "../diskFormat";
import { cleanDisplayTitle } from "../titles";

interface Candidate {
  tmdb_id: number;
  title: string;
  year: number | null;
  media_type: string;
  original_title?: string | null;
  poster_url?: string | null;
  overview?: string | null;
  season_count?: number | null;
  episode_count?: number | null;
  runtime?: number | null;
}

interface ReviewSeason {
  season: number;
  episodes: number;
  label: string;
}

interface ReviewFolder {
  path: string;
  name: string;
  title: string;
  year: number | null;
  media_type: string;
  tmdb_id: number | null;
  file_count: number;
  canonical: boolean;
  seasons: ReviewSeason[];
}

interface ReviewMove {
  src: string;
  dest: string;
  name: string;
  blocked: string | null;
}

interface ReviewFile {
  path: string;
  name: string;
  kind: string;
  reason: string;
  season: number | null;
  episode: number | null;
  target_path: string;
  target_name: string;
  blocked: string | null;
  original_name?: string | null;
  action?: string;
  size?: number;
  other_size?: number | null;
  warning?: string | null;
}

interface ReviewSeries {
  key: string;
  title: string;
  year: number | null;
  tmdb_id: number | null;
  poster_url: string | null;
  media_type: string;
  media_id: number | null;
  followed: boolean;
  problem: string | null;
  duplicate: boolean;
  merge_hint: string | null;
  folders: ReviewFolder[];
  merges: ReviewMove[];
  files: ReviewFile[];
  empty_after?: string[];
}

interface SuspectFile {
  path: string;
  name: string;
  size: number;
  media_id: number | null;
  message: string;
}

interface ReviewReport {
  series: ReviewSeries[];
  empty: EmptyFolder[];
  mismatch: number;
  duplicate_folders: number;
  unmatched: number;
  empty_count: number;
  suspect_files?: SuspectFile[];
}

interface RegroupMove {
  src: string;
  dest: string;
  name: string;
  original_name: string;
  blocked: string | null;
  action?: string;
  size?: number;
  other_size?: number | null;
  warning?: string | null;
}

interface RegroupGroup {
  media_id: number | null;
  title: string;
  year: number | null;
  tmdb_id: number | null;
  media_type: string;
  canonical: string;
  match: string;
  folders: string[];
  moves: RegroupMove[];
  empty_after: string[];
  loose?: boolean;
}

interface RegroupReport {
  groups: RegroupGroup[];
  unmatched: string[];
  ambiguous: string[];
  empty: string[];
  loose?: RegroupGroup[];
  rename_id: number | null;
  applied?: boolean;
}

interface IncompleteFile {
  path: string;
  name: string;
  size: number;
  reason: string;
  written_bytes: number;
  reserved_bytes: number;
  job_id: number | null;
  job_state: string;
  orphan: boolean;
}

interface IncompleteReport {
  files: IncompleteFile[];
  moved: number;
  failed?: number;
  errors?: string[];
  trash?: string;
  auto_enabled?: boolean;
  last_run_at?: string | null;
  auto_message?: string;
  folder_written_bytes: number;
  folder_reserved_bytes: number;
  total_written_bytes: number;
  trash_ids?: number[];
}

interface AlignChange {
  media_id: number;
  title: string;
  from_tmdb?: number | null;
  to_tmdb?: number | null;
}

interface AlignMerge {
  title: string;
}

interface AlignOrphan {
  title: string;
}

interface AlignRename {
  src: string;
  dest: string;
  blocked: string | null;
}

interface AlignPush {
  media_id: number | null;
  tmdb_id: number;
  state: string;
  attempts: number;
  last_error: string | null;
  max_attempts?: number;
}

interface AlignReport {
  scanning: boolean;
  progress: number | null;
  message: string;
  auto_rename: boolean;
  refreshed: number;
  attaches: AlignChange[];
  reidentifies: AlignChange[];
  merges: AlignMerge[];
  orphans: AlignOrphan[];
  cleaned_after_watch: AlignOrphan[];
  holds: AlignChange[];
  renames: AlignRename[];
  pushes: AlignPush[];
}

interface Suggestion {
  tmdb_id: number;
  title: string;
  year: number | null;
  media_type?: string;
  original_title?: string | null;
  poster_url?: string | null;
  overview?: string | null;
  season_count?: number | null;
  episode_count?: number | null;
  runtime?: number | null;
}

interface ManualPanel {
  path: string;
  query: string;
  type: string;
  tmdbId: string;
  preview: Candidate | null;
  results: Candidate[];
  searching: boolean;
}

const review = ref<ReviewReport | null>(null);
const regroup = ref<RegroupReport | null>(null);
const route = useRoute();
const router = useRouter();
const { jellyfinConfigured, serviceStatusLoaded } = useServiceStatus();
const jellyfinAlignVisible = computed(() => serviceStatusLoaded.value && jellyfinConfigured.value);

type LibraryTab = "a-traiter" | "nettoyage" | "contenu";
const LIBRARY_TABS: { id: LibraryTab; label: string }[] = [
  { id: "a-traiter", label: "À traiter" },
  { id: "nettoyage", label: "Nettoyage" },
  { id: "contenu", label: "Contenu" },
];

const activeTab = ref<LibraryTab>("a-traiter");
const incomplete = ref<IncompleteReport | null>(null);
const incompleteStatus = ref("");
const incompleteStep = ref<"list" | "preview" | "done">("list");
const incompleteSelection = ref<string[]>([]);
const incompleteUndoIds = ref<number[]>([]);
const incompleteBusy = ref(false);
const align = ref<AlignReport | null>(null);
const alignStep = ref(0);
const openGroups = ref<Record<string, boolean>>({});
const suggestions = ref<Record<string, Suggestion[]>>({});
const renameId = ref<number | null>(null);
const error = ref("");
const message = ref("");
const busy = ref(false);
interface EmptyFolder {
  path: string;
  file_count: number;
  size: number;
  summary: string;
}

const manual = ref<ManualPanel | null>(null);
const quick = ref<QuickTarget | null>(null);
const quickFollow = ref(false);
const reviewGroups = computed(() => review.value?.series ?? []);
const isFilmGroup = (row: ReviewSeries) => row.media_type === "film";
const problemFilms = computed(() => reviewGroups.value.filter((row) => row.problem && isFilmGroup(row)));
const problemSeries = computed(() => reviewGroups.value.filter((row) => row.problem && !isFilmGroup(row)));
const calmFilms = computed(() => reviewGroups.value.filter((row) => !row.problem && isFilmGroup(row)));
const calmSeries = computed(() => reviewGroups.value.filter((row) => !row.problem && !isFilmGroup(row)));

const problemSections = computed(() =>
  [
    { id: "problems-series", label: "Séries à ranger", groups: problemSeries.value },
    { id: "problems-films", label: "Films à ranger", groups: problemFilms.value },
  ].filter((block) => block.groups.length),
);

const contentSections = computed(() => [
  { id: "content-films", label: "Films OK", emptyFilter: "Aucun film dans ce filtre.", groups: calmFilms.value },
  { id: "content-series", label: "Séries OK", emptyFilter: "Aucune série dans ce filtre.", groups: calmSeries.value },
]);

const SECTION_STORAGE_KEY = "mediatracker.library.sections.v2";
const sectionOpen = ref<Record<string, boolean>>({});

interface CalmMediaMeta {
  on_hold: boolean;
  followed?: boolean;
  watched?: boolean;
  on_disk?: boolean;
  availability?: string;
  disk_label?: string | null;
  follow_origin?: string;
  created_at?: string;
  file_absent?: boolean;
  tmdb_id?: number | null;
}

const calmMediaById = ref<Record<number, CalmMediaMeta>>({});
const contentQuery = ref("");
const contentSort = ref<"alpha" | "added">("alpha");
const contentFilter = ref<"all" | "verified" | "missing" | "unidentified">("all");

function sectionExpanded(id: string, fallback = false): boolean {
  const stored = sectionOpen.value[id];
  return stored === undefined ? fallback : stored;
}

function setSectionOpen(id: string, open: boolean) {
  sectionOpen.value = { ...sectionOpen.value, [id]: open };
  try {
    localStorage.setItem(SECTION_STORAGE_KEY, JSON.stringify(sectionOpen.value));
  } catch {
    /* private mode */
  }
}

function toggleSection(id: string, fallback = false) {
  setSectionOpen(id, !sectionExpanded(id, fallback));
}

function loadSectionPrefs() {
  try {
    const stored = localStorage.getItem(SECTION_STORAGE_KEY);
    if (stored) {
      sectionOpen.value = JSON.parse(stored) as Record<string, boolean>;
    }
  } catch {
    sectionOpen.value = {};
  }
}

const alignPendingCount = computed(() => {
  if (!align.value) {
    return 0;
  }
  const row = align.value;
  return (
    row.attaches.length +
    row.reidentifies.length +
    row.merges.length +
    row.orphans.length +
    row.renames.filter((move) => !move.blocked).length
  );
});

const regroupCount = computed(() => {
  if (!regroup.value) {
    return 0;
  }
  return (
    regroup.value.groups.length +
    (regroup.value.loose?.length ?? 0) +
    regroup.value.unmatched.length +
    regroup.value.ambiguous.length
  );
});

const cleanupCount = computed(() => review.value?.empty.length ?? 0);
const suspectFiles = computed(() => review.value?.suspect_files ?? []);
const problemsCount = computed(() => problemFilms.value.length + problemSeries.value.length);

const aTraiterCount = computed(() => {
  let total = problemsCount.value + suspectFiles.value.length + regroupCount.value;
  if (jellyfinAlignVisible.value && align.value && alignPendingCount.value) {
    total += alignPendingCount.value;
  }
  if (renameId.value != null) {
    total += 1;
  }
  return total;
});

const nettoyageCount = computed(() => (incomplete.value?.files.length ?? 0) + cleanupCount.value);

const contenuCount = computed(() => calmFilms.value.length + calmSeries.value.length);

function tabCount(tab: LibraryTab): number {
  if (tab === "a-traiter") {
    return aTraiterCount.value;
  }
  if (tab === "nettoyage") {
    return nettoyageCount.value;
  }
  return contenuCount.value;
}

function readLibraryTabFromRoute(): void {
  const raw = typeof route.query.onglet === "string" ? route.query.onglet : "";
  activeTab.value = LIBRARY_TABS.some((row) => row.id === raw) ? (raw as LibraryTab) : "a-traiter";
  if (route.query.incomplets === "corbeille") {
    activeTab.value = "nettoyage";
  }
}

function syncLibraryTabQuery(): void {
  const query = { ...route.query } as Record<string, string | string[] | undefined>;
  if (activeTab.value === "a-traiter") {
    delete query.onglet;
  } else {
    query.onglet = activeTab.value;
  }
  void router.replace({ query });
}

function setLibraryTab(tab: LibraryTab): void {
  if (activeTab.value === tab) {
    return;
  }
  activeTab.value = tab;
  syncLibraryTabQuery();
}

function contentMatchesFilter(group: ReviewSeries, key: typeof contentFilter.value): boolean {
  const media = group.media_id != null ? calmMediaById.value[group.media_id] : null;
  if (key === "unidentified") {
    return group.tmdb_id == null || group.media_id == null;
  }
  if (key === "missing") {
    return Boolean(media?.file_absent);
  }
  if (key === "verified") {
    return group.media_id != null && group.tmdb_id != null && !media?.file_absent;
  }
  return true;
}

function visibleContentGroups(source: ReviewSeries[]): ReviewSeries[] {
  const needle = contentQuery.value.trim().toLocaleLowerCase("fr");
  const rows = source.filter((group) => {
    const title = (cleanDisplayTitle(group.title) || group.title).toLocaleLowerCase("fr");
    if (needle && !title.includes(needle)) {
      return false;
    }
    return contentMatchesFilter(group, contentFilter.value);
  });
  return [...rows].sort((left, right) => {
    if (contentSort.value === "added") {
      const leftAt = left.media_id != null ? calmMediaById.value[left.media_id]?.created_at || "" : "";
      const rightAt = right.media_id != null ? calmMediaById.value[right.media_id]?.created_at || "" : "";
      return rightAt.localeCompare(leftAt);
    }
    const leftTitle = cleanDisplayTitle(left.title) || left.title;
    const rightTitle = cleanDisplayTitle(right.title) || right.title;
    return leftTitle.localeCompare(rightTitle, "fr");
  });
}

function countContentForFilter(key: typeof contentFilter.value, groups: ReviewSeries[]): number {
  if (key === "all") {
    return groups.length;
  }
  return groups.filter((group) => contentMatchesFilter(group, key)).length;
}

function mismatchSeriesLabel(count: number): string {
  if (count === 1) {
    return "1 fichier dans la mauvaise série";
  }
  return `${count} fichiers dans la mauvaise série`;
}

async function loadCalmMedia() {
  const ids = new Set(
    [...calmFilms.value, ...calmSeries.value]
      .map((group) => group.media_id)
      .filter((id): id is number => id != null),
  );
  if (!ids.size) {
    calmMediaById.value = {};
    return;
  }
  try {
    const body = await api<{
      items: {
        id: number;
        on_hold: boolean;
        availability?: string;
        disk_label?: string | null;
        follow_origin?: string;
        created_at?: string;
        followed?: boolean;
        watched?: boolean;
        on_disk?: boolean;
        file_absent?: boolean;
        tmdb_id?: number | null;
      }[];
    }>("/api/v1/media");
    const map: Record<number, CalmMediaMeta> = {};
    for (const item of body.items) {
      if (ids.has(item.id)) {
        map[item.id] = {
          on_hold: item.on_hold,
          followed: item.followed,
          watched: item.watched,
          on_disk: item.on_disk,
          availability: item.availability,
          disk_label: item.disk_label,
          follow_origin: item.follow_origin,
          created_at: item.created_at,
          file_absent: item.file_absent,
          tmdb_id: item.tmdb_id,
        };
      }
    }
    calmMediaById.value = map;
  } catch {
    calmMediaById.value = {};
  }
}

const {
  running: verifyAllRunning,
  starting: verifyAllStarting,
  lastSummary: verifyAllSummary,
  progressLine: verifyAllProgressLine,
  progressPercent: verifyAllProgressPercent,
  resultHeadline: verifyAllResultHeadline,
  resultActionsLine: verifyAllResultActionsLine,
  buttonLabel: verifyAllButtonLabel,
  buttonDisabled: verifyAllButtonDisabled,
  fetchError: verifyAllFetchError,
  start: startVerifyAll,
} = useLibraryVerifyAll(() => {
  void analyze();
});

const libraryHeadStats = computed(() => {
  if (!review.value) {
    return "";
  }
  const films = reviewGroups.value.filter(isFilmGroup).length;
  const series = reviewGroups.value.filter((row) => !isFilmGroup(row)).length;
  return `${films} film${films > 1 ? "s" : ""} · ${series} série${series > 1 ? "s" : ""} · ${aTraiterCount.value} à traiter`;
});

function openSeries(group: ReviewSeries, follow = false) {
  quickFollow.value = follow && !group.followed;
  quick.value = {
    title: cleanDisplayTitle(group.title) || group.title,
    year: group.year,
    posterUrl: group.poster_url,
    mediaType: group.media_type,
    followed: group.followed,
    tmdbId: group.tmdb_id,
    tmdbUrl: group.tmdb_id ? tmdbPage(group.tmdb_id, group.media_type) : null,
    mediaId: group.media_id,
  };
}

function openCandidate(event: MouseEvent, id: number, type: string) {
  const target = event.target as HTMLElement | null;
  if (target?.closest("button, select, a")) {
    return;
  }
  window.open(tmdbPage(id, type), "_blank", "noopener,noreferrer");
}

function toggleGroup(key: string) {
  openGroups.value = { ...openGroups.value, [key]: !openGroups.value[key] };
}

function fileMoves(group: ReviewSeries): ReviewFile[] {
  return group.files.filter((file) => file.action !== "trash" && file.target_path && !file.blocked);
}

function trashMoves(group: ReviewSeries): ReviewFile[] {
  return group.files.filter((file) => file.action === "trash" && !file.blocked);
}

function kindLabel(kind: string): string {
  if (kind === "mismatch") return "Mauvaise série";
  if (kind === "duplicate") return "Doublon";
  return "À revoir";
}

async function followFolder(folder: ReviewFolder, follow: boolean) {
  busy.value = true;
  error.value = "";
  try {
    const result = await api<{ message: string; followed: boolean }>("/api/v1/library/folders/follow", {
      method: "POST",
      body: JSON.stringify({ path: folder.path, follow }),
    });
    message.value = result.message;
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Suivi impossible.");
  } finally {
    busy.value = false;
  }
}

const incompleteSelected = computed(() => {
  const chosen = new Set(incompleteSelection.value);
  return (incomplete.value?.files ?? []).filter((file) => chosen.has(file.path));
});

const incompleteSelectedBytes = computed(() =>
  incompleteSelected.value.reduce((sum, file) => sum + (file.written_bytes || file.size), 0),
);

function syncIncompleteSelection() {
  incompleteSelection.value = (incomplete.value?.files ?? []).map((file) => file.path);
}

function openIncompleteFromRoute() {
  if (route.query.incomplets !== "corbeille") {
    return;
  }
  activeTab.value = "nettoyage";
  if (!incomplete.value?.files.length) {
    return;
  }
  setSectionOpen("incomplete", true);
  incompleteStep.value = incomplete.value.files.length ? "list" : "done";
}

async function loadIncomplete() {
  incompleteBusy.value = true;
  try {
    incomplete.value = await api<IncompleteReport>("/api/v1/library/incomplete");
    syncIncompleteSelection();
    if (incompleteStep.value === "done" && !incomplete.value.files.length) {
      incompleteStep.value = "done";
    } else if (!incomplete.value.files.length) {
      incompleteStep.value = "list";
    }
    openIncompleteFromRoute();
  } catch (err) {
    if (!error.value) {
      error.value = userFacingApiError(err, "Incomplets indisponibles.");
    }
  } finally {
    incompleteBusy.value = false;
  }
}

async function analyze() {
  error.value = "";
  busy.value = true;
  try {
    review.value = await api<ReviewReport>("/api/v1/library/review");
    regroup.value = await api<RegroupReport>("/api/v1/library/regroup");
    align.value = await api<AlignReport>("/api/v1/library/jellyfin");
  } catch (err) {
    error.value = userFacingApiError(err, "Analyse impossible.");
  }
  await loadIncomplete();
  await loadCalmMedia();
  busy.value = false;
}

async function refreshJellyfin(full = false) {
  error.value = "";
  busy.value = true;
  try {
    align.value = await api<AlignReport>(`/api/v1/library/jellyfin/refresh?full=${full ? "true" : "false"}`, {
      method: "POST",
    });
    message.value = align.value.scanning
      ? align.value.message
      : `Catalogue Jellyfin relu (${align.value.refreshed} élément(s)).`;
  } catch (err) {
    error.value = userFacingApiError(err, "Lecture Jellyfin impossible.");
  } finally {
    busy.value = false;
  }
}

async function applyAlignment() {
  if (!align.value || align.value.scanning) {
    return;
  }
  const bits = [
    align.value.attaches.length ? `${align.value.attaches.length} rattachement(s)` : "",
    align.value.reidentifies.length ? `${align.value.reidentifies.length} correction(s) d'identifiant` : "",
    align.value.merges.length ? `${align.value.merges.length} fusion(s)` : "",
  ].filter(Boolean);
  if (!window.confirm(`Appliquer l'alignement Jellyfin ? ${bits.join(", ") || "Aucun changement de fiche."} Aucun fichier n'est déplacé.`)) {
    return;
  }
  error.value = "";
  busy.value = true;
  try {
    align.value = await api<AlignReport>("/api/v1/library/jellyfin/align", {
      method: "POST",
      body: JSON.stringify({ validated: true }),
    });
    alignStep.value = Math.max(alignStep.value, 1);
    message.value = align.value.message;
  } catch (err) {
    error.value = userFacingApiError(err, "Alignement impossible.");
  } finally {
    busy.value = false;
  }
}

async function applyJellyfinRename() {
  if (!align.value || align.value.scanning || alignStep.value < 1) {
    return;
  }
  const open = align.value.renames.filter((row) => !row.blocked);
  if (!open.length) {
    alignStep.value = Math.max(alignStep.value, 2);
    message.value = "Rien à ranger. L'étape suivante est disponible.";
    return;
  }
  if (!window.confirm(`${open.length} fichier(s) vont prendre le nom Title (Year) [tmdbid-N]. Jellyfin sera rafraîchi ensuite.`)) {
    return;
  }
  error.value = "";
  busy.value = true;
  try {
    align.value = await api<AlignReport>("/api/v1/library/jellyfin/rename", {
      method: "POST",
      body: JSON.stringify({ validated: true }),
    });
    alignStep.value = Math.max(alignStep.value, 2);
    message.value = align.value.message;
  } catch (err) {
    error.value = userFacingApiError(err, "Rangement impossible.");
  } finally {
    busy.value = false;
  }
}

function finishAlignment() {
  if (!align.value || align.value.scanning || alignStep.value < 2) {
    return;
  }
  alignStep.value = 3;
  message.value = "Alignement Jellyfin terminé.";
}

function pushLabel(row: AlignPush): string {
  const err = row.last_error ? ` — ${row.last_error}` : "";
  const maxAttempts = row.max_attempts ?? 5;
  const attempt = row.state === "pending" && row.attempts ? `, essai ${row.attempts}/${maxAttempts}` : "";
  return `${row.tmdb_id} (${row.state}${attempt}${err})`;
}

function incompleteSummary(result: IncompleteReport, bytes = 0): string {
  const moved = result.moved ?? 0;
  const failed = result.failed ?? 0;
  const parts: string[] = [];
  if (moved) {
    const size = bytes ? ` (${formatBytes(bytes)})` : "";
    parts.push(`${moved} fichier(s) déplacé(s) vers la corbeille${size}.`);
  }
  if (failed) {
    parts.push(`${failed} échec(s).`);
  }
  if (result.errors?.length) {
    parts.push(result.errors.join(" "));
  }
  return parts.join(" ") || "Rien n'a été déplacé.";
}

function toggleIncomplete(path: string) {
  const set = new Set(incompleteSelection.value);
  if (set.has(path)) {
    set.delete(path);
  } else {
    set.add(path);
  }
  incompleteSelection.value = [...set];
}

function selectAllIncomplete() {
  syncIncompleteSelection();
}

function showIncompletePreview() {
  if (!incompleteSelected.value.length) {
    return;
  }
  incompleteStep.value = "preview";
}

function backToIncompleteList() {
  incompleteStep.value = "list";
}

async function applyIncomplete() {
  if (!incompleteSelected.value.length) {
    return;
  }
  busy.value = true;
  error.value = "";
  const bytes = incompleteSelectedBytes.value;
  try {
    const result = await api<IncompleteReport>("/api/v1/library/incomplete", {
      method: "POST",
      body: JSON.stringify({ validated: true, paths: incompleteSelected.value.map((file) => file.path) }),
    });
    incomplete.value = result;
    incompleteUndoIds.value = result.trash_ids ?? [];
    const note = incompleteSummary(result, bytes);
    incompleteStatus.value = note;
    message.value = note;
    incompleteStep.value = "done";
    syncIncompleteSelection();
    await analyze();
    if ((result.failed ?? 0) > 0) {
      error.value = result.errors?.join(" ") || note;
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Nettoyage des incomplets impossible.");
  } finally {
    busy.value = false;
  }
}

async function undoIncompleteTrash() {
  if (!incompleteUndoIds.value.length) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    for (const entryId of incompleteUndoIds.value) {
      await api(`/api/v1/trash/${entryId}/restore`, { method: "POST" });
    }
    incompleteUndoIds.value = [];
    incompleteStatus.value = "";
    incompleteStep.value = "list";
    message.value = "Fichiers remis en place.";
    await loadIncomplete();
  } catch (err) {
    error.value = userFacingApiError(err, "Annulation impossible.");
  } finally {
    busy.value = false;
  }
}

function incompleteFolderLine(report: IncompleteReport): string {
  return fileSizeLine(report.folder_written_bytes, report.folder_reserved_bytes);
}

async function deleteEmpty(paths: string[]) {
  if (!paths.length) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const body = await api<{ paths: string[] }>("/api/v1/library/empty-folders", {
      method: "POST",
      body: JSON.stringify({ paths }),
    });
    message.value = `${body.paths.length} élément(s) mis à la corbeille.`;
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Nettoyage impossible.");
  } finally {
    busy.value = false;
  }
}

async function cleanEmpty() {
  const paths = review.value?.empty.map((folder) => folder.path) ?? [];
  if (!paths.length) {
    return;
  }
  if (!window.confirm(`Mettre ${paths.length} élément(s) à la corbeille ? Les vidéos restent. Rien n'est effacé.`)) {
    return;
  }
  await deleteEmpty(paths);
}

async function removeOne(folder: EmptyFolder) {
  const detail = folder.summary ? `\n${folder.summary}` : "";
  if (!window.confirm(`Mettre à la corbeille ?\n${folder.path}${detail}\n\nLes fichiers ne sont pas effacés.`)) {
    return;
  }
  await deleteEmpty([folder.path]);
}

function openManual(folder: ReviewFolder, query: string) {
  manual.value = {
    path: folder.path,
    query: query || folder.title || folder.name,
    type: folder.media_type === "film" ? "film" : "series",
    tmdbId: "",
    preview: null,
    results: [],
    searching: false,
  };
  openGroups.value = { ...openGroups.value, [folder.path]: true };
}

async function identify(folder: ReviewFolder, redo = false) {
  error.value = "";
  busy.value = true;
  try {
    const result = await api<{
      tmdb_id: number | null;
      confidence: string;
      source: string;
      title: string;
      suggestions: Suggestion[];
    }>("/api/v1/library/folders/identify", {
      method: "POST",
      body: JSON.stringify({ path: folder.path }),
    });
    suggestions.value = { ...suggestions.value, [folder.path]: result.suggestions };
    openGroups.value = { ...openGroups.value, [folder.path]: true };
    openManual(folder, result.title || folder.title);
    if (!redo && result.tmdb_id != null && result.confidence !== "ambiguous" && result.confidence !== "none") {
      manual.value = manual.value ? { ...manual.value, tmdbId: String(result.tmdb_id) } : manual.value;
      message.value = "Correspondance proposée. Le dossier n'est pas modifié tant que vous n'appliquez pas.";
    }
  } catch (err) {
    error.value = userFacingApiError(err, "Identification impossible.");
    openManual(folder, folder.title);
  } finally {
    busy.value = false;
  }
}

async function applyFolder(folder: ReviewFolder, tmdbId: number, type: string) {
  error.value = "";
  busy.value = true;
  try {
    const result = await api<{ path: string; title: string; attached: number }>("/api/v1/library/folders/bind", {
      method: "POST",
      body: JSON.stringify({ path: folder.path, tmdb_id: tmdbId, type }),
    });
    message.value = `${result.title} : ${result.attached} fichier(s) rattaché(s).`;
    manual.value = null;
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Association impossible.");
  } finally {
    busy.value = false;
  }
}

async function searchManual() {
  if (!manual.value || manual.value.query.trim().length < 2) {
    return;
  }
  manual.value.searching = true;
  error.value = "";
  try {
    const params = new URLSearchParams({
      q: manual.value.query.trim(),
      type: manual.value.type,
      detail: "true",
    });
    const body = await api<{ items: Candidate[] }>(`/api/v1/metadata/tmdb/search?${params.toString()}`);
    manual.value.results = body.items;
  } catch (err) {
    error.value = userFacingApiError(err, "Recherche TMDB impossible.");
  } finally {
    manual.value.searching = false;
  }
}

async function previewManual() {
  if (!manual.value) {
    return;
  }
  const id = Number(manual.value.tmdbId);
  if (!Number.isInteger(id) || id < 1) {
    error.value = "Indiquez un identifiant TMDB.";
    return;
  }
  error.value = "";
  try {
    manual.value.preview = await api<Candidate>(`/api/v1/metadata/tmdb/${manual.value.type}/${id}`);
  } catch (err) {
    manual.value.preview = null;
    error.value = userFacingApiError(err, "Aperçu TMDB impossible.");
  }
}

function openMoves(group: RegroupGroup): RegroupMove[] {
  return group.moves.filter((move) => !move.blocked);
}

function regroupTally(groups: RegroupGroup[]) {
  let moves = 0;
  let trash = 0;
  let blocked = 0;
  for (const group of groups) {
    for (const move of group.moves) {
      if (move.blocked) {
        blocked += 1;
      } else if (move.action === "trash") {
        trash += 1;
      } else {
        moves += 1;
      }
    }
  }
  const emptied = groups.reduce((sum, group) => sum + group.empty_after.length, 0);
  return { fiches: groups.length, moves, trash, blocked, emptied };
}

function regroupSummary(groups: RegroupGroup[]): string {
  const tally = regroupTally(groups);
  return `${tally.fiches} fiche(s) : ${tally.moves} fichier(s) vers Title (Year) [tmdbid], ${tally.trash} doublon(s) vers la corbeille, ${tally.blocked} bloqué(s), ${tally.emptied} dossier(s) vidé(s) ensuite.`;
}

function matchLabel(match: string): string {
  if (match === "tmdb") {
    return "Correspondance";
  }
  if (match === "fichier") {
    return "Nom de fichier";
  }
  return "Titre";
}

async function applyRegroup(groups: RegroupGroup[], loose = false) {
  const ids = groups.map((group) => group.media_id).filter((id): id is number => id != null);
  const count = groups.reduce((sum, group) => sum + openMoves(group).length, 0);
  if ((!ids.length && !loose) || !count) {
    return;
  }
  const summary = regroupSummary(groups);
  const looseNote = loose ? "\nLes copies non normées sans fiche partent aussi à la corbeille." : "";
  if (!window.confirm(`${summary}${looseNote}\n\nAppliquer ce regroupement ? Les doublons vont à la corbeille, ils ne sont pas effacés.`)) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const result = await api<RegroupReport>("/api/v1/library/regroup", {
      method: "POST",
      body: JSON.stringify({ validated: true, media_ids: ids, include_loose: loose }),
    });
    regroup.value = result;
    if (result.rename_id != null) {
      renameId.value = result.rename_id;
    }
    message.value = result.applied
      ? "Regroupement appliqué. Annuler restaure les déplacements. Les fichiers à la corbeille y restent."
      : "Rien n'a été déplacé.";
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Regroupement impossible.");
  } finally {
    busy.value = false;
  }
}

function trashPreview(files: ReviewFile[]): string {
  const lines = files.map((file) => {
    const kept = file.other_size != null ? ` · copie gardée ${formatBytes(file.other_size)}` : "";
    const warn = file.warning ? `\nÀ décider : ${file.warning}` : "";
    return `${file.name}\n${formatBytes(file.size || 0)}${kept}${warn}`;
  });
  return `Mettre à la corbeille ?\n${lines.join("\n\n")}\n\nLes fichiers ne sont pas effacés.`;
}

async function applyFiles(paths: string[], trash = false, files: ReviewFile[] = []) {
  if (!paths.length) {
    return;
  }
  const prompt = trash ? trashPreview(files) : `Déplacer ${paths.length} fichier(s) vers le dossier proposé ?`;
  if (!window.confirm(prompt)) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const result = await api<{ id: number | null; applied: boolean; moves: number }>("/api/v1/library/review/files", {
      method: "POST",
      body: JSON.stringify({ paths, validated: true }),
    });
    renameId.value = result.id;
    message.value = result.applied
      ? trash
        ? `${result.moves} fichier(s) mis à la corbeille. Ils ne sont pas effacés.`
        : `${result.moves} fichier(s) déplacé(s). Annuler restaure l'ancien chemin.`
      : "Rien n'a été déplacé.";
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Déplacement impossible.");
  } finally {
    busy.value = false;
  }
}

async function applyMerge(group: ReviewSeries) {
  const count = group.merges.filter((move) => !move.blocked).length;
  if (!count) {
    return;
  }
  if (!window.confirm(`${group.merge_hint}\n\nDéplacer ${count} fichier(s) ?`)) {
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const result = await api<{ id: number | null; applied: boolean; moves: number }>("/api/v1/library/review/merge", {
      method: "POST",
      body: JSON.stringify({ key: group.key, validated: true }),
    });
    renameId.value = result.id;
    message.value = result.applied ? "Fichiers regroupés dans le dossier de référence." : "Rien n'a été déplacé.";
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Regroupement impossible.");
  } finally {
    busy.value = false;
  }
}

async function undoRename() {
  if (renameId.value == null) {
    return;
  }
  error.value = "";
  try {
    await api("/api/v1/library/folders/undo", {
      method: "POST",
      body: JSON.stringify({ id: renameId.value }),
    });
    renameId.value = null;
    await analyze();
  } catch (err) {
    error.value = userFacingApiError(err, "Annulation impossible.");
  }
}

watch(
  () => route.query.incomplets,
  () => {
    openIncompleteFromRoute();
  },
);

watch(
  () => route.query.onglet,
  () => {
    readLibraryTabFromRoute();
  },
);

onMounted(async () => {
  loadSectionPrefs();
  readLibraryTabFromRoute();
  await analyze();
  if (incomplete.value?.files.length && sectionOpen.value.incomplete === undefined) {
    setSectionOpen("incomplete", true);
  }
});
</script>

<template>
  <section class="stack library-page">
    <header class="library-head">
      <div class="library-head-copy">
        <h1>Bibliothèque</h1>
        <p class="hint library-head-intro">
          Fichiers sur le disque uniquement. Rien n'est déplacé sans confirmation.
        </p>
        <p v-if="review" class="hint library-head-stats">{{ libraryHeadStats }}</p>
      </div>
      <div class="actions library-head-actions library-verify-toolbar" aria-label="Actions bibliothèque">
        <button type="button" class="primary" :disabled="busy" data-library-analyze @click="analyze">
          <StatusIcon name="search" label="Analyser" /> Analyser
        </button>
        <button
          type="button"
          data-verify-all-start
          :disabled="verifyAllButtonDisabled || busy"
          @click="startVerifyAll()"
        >
          <StatusIcon
            name="refresh"
            label="Tout vérifier"
            :spin="verifyAllRunning || verifyAllStarting"
          />
          {{ verifyAllButtonLabel }}
        </button>
        <button type="button" :disabled="busy || incompleteBusy" data-library-rescan @click="loadIncomplete">
          Relire les incomplets
        </button>
      </div>
    </header>

    <nav class="library-tabs" role="tablist" aria-label="Segments bibliothèque">
      <button
        v-for="entry in LIBRARY_TABS"
        :key="entry.id"
        type="button"
        role="tab"
        :class="{ active: activeTab === entry.id }"
        :aria-selected="activeTab === entry.id"
        :data-library-tab="entry.id"
        @click="setLibraryTab(entry.id)"
      >
        {{ entry.label }} · {{ tabCount(entry.id) }}
      </button>
    </nav>

    <article
      v-if="verifyAllRunning || verifyAllSummary"
      class="panel stack library-verify"
      data-verify-all-panel
    >
      <template v-if="verifyAllRunning">
        <p class="library-verify-status icon-line">
          <span class="health-dot health-dot-muted" aria-hidden="true" />
          <strong>Vérification en cours</strong>
        </p>
        <p class="hint">{{ verifyAllProgressLine }}</p>
        <span class="track library-verify-track" aria-hidden="true">
          <span class="fill" :style="{ width: `${verifyAllProgressPercent}%` }" />
        </span>
        <p class="hint">Vous pouvez quitter la page : la vérification continue en arrière-plan.</p>
      </template>
      <template v-else-if="verifyAllSummary">
        <p class="library-verify-status icon-line success-line">
          <span class="health-dot health-dot-ok" aria-hidden="true" />
          <span>{{ verifyAllResultHeadline }}</span>
        </p>
        <p v-if="verifyAllResultActionsLine" class="hint">{{ verifyAllResultActionsLine }}</p>
        <ul v-if="verifyAllSummary.problems.length" class="entry-list library-verify-problems">
          <li v-for="problem in verifyAllSummary.problems" :key="problem.media_id">
            <div class="entry-main">
              <span class="entry-copy">
                <strong>
                  <router-link :to="`/medias/${problem.media_id}`">{{ problem.title }}</router-link>
                </strong>
                <span class="hint">{{ problem.summary }}</span>
              </span>
            </div>
            <div class="entry-actions">
              <router-link class="button" :to="`/medias/${problem.media_id}`">Ouvrir la fiche</router-link>
            </div>
          </li>
        </ul>
        <details class="library-verify-details">
          <summary>
            Détails
            <span v-if="verifyAllSummary.problems.length" class="hint">
              · {{ verifyAllSummary.problems.length }} fiche(s)
            </span>
          </summary>
          <p v-if="verifyAllSummary.detail" class="hint">{{ verifyAllSummary.detail }}</p>
          <ul v-if="verifyAllSummary.problems.length" class="entry-list">
            <li v-for="problem in verifyAllSummary.problems" :key="`detail-${problem.media_id}`">
              <router-link :to="`/medias/${problem.media_id}`">{{ problem.title }}</router-link>
              <span class="hint">
                · {{ problem.summary }}
                <template v-if="problem.files_missing"> · {{ problem.files_missing }} absent(s)</template>
              </span>
            </li>
          </ul>
        </details>
      </template>
    </article>
    <p v-if="verifyAllFetchError" class="error">{{ verifyAllFetchError }}</p>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="message" class="hint">{{ message }}</p>

    <p v-if="review && activeTab === 'a-traiter'" class="hint">
      {{ mismatchSeriesLabel(review.mismatch) }},
      {{ review.duplicate_folders }} série(s) avec des dossiers en double,
      {{ review.unmatched }} dossier(s) non reconnu(s),
      {{ review.empty_count }} dossier(s) vide(s).
    </p>

    <section
      v-if="suspectFiles.length"
      v-show="activeTab === 'a-traiter'"
      class="library-section panel stack"
      data-library-section="suspect"
      data-library-suspect-files
    >
      <h2 class="library-section-heading">
        <button
          type="button"
          class="library-section-toggle"
          :aria-expanded="sectionExpanded('suspect', true)"
          @click="toggleSection('suspect', true)"
        >
          <span class="library-section-chevron" aria-hidden="true" />
          <span class="library-section-label">Fichiers suspects</span>
          <span class="library-section-count">{{ suspectFiles.length }}</span>
        </button>
      </h2>
      <div v-show="sectionExpanded('suspect', true)" class="library-section-body stack">
        <ul class="entry-list library-verify-problems">
          <li v-for="file in suspectFiles" :key="file.path">
            <div class="entry-main">
              <span class="entry-copy">
                <strong>{{ file.name }}</strong>
                <span class="hint">{{ file.message }}</span>
              </span>
            </div>
            <div v-if="file.media_id" class="entry-actions">
              <router-link class="button" :to="`/medias/${file.media_id}`">Ouvrir la fiche</router-link>
            </div>
          </li>
        </ul>
      </div>
    </section>

    <section v-if="align && jellyfinAlignVisible" v-show="activeTab === 'a-traiter'" class="library-section panel stack" data-library-section="align">
      <h2 class="library-section-heading">
        <button
          type="button"
          class="library-section-toggle"
          :aria-expanded="sectionExpanded('align', false)"
          @click="toggleSection('align', false)"
        >
          <span class="library-section-chevron" aria-hidden="true" />
          <span class="library-section-label">Alignement Jellyfin</span>
          <span class="library-section-count">{{ alignPendingCount }}</span>
        </button>
      </h2>
      <div v-show="sectionExpanded('align', false)" class="library-section-body stack">
      <p class="hint">
        La dernière écriture gagne. Une modification ici est poussée vers Jellyfin ;
        une sauvegarde Jellyfin plus récente est reprise, puis les dossiers suivent
        Title (Year) [tmdbid-N]/Season NN. Rien n'est déplacé tant que le rangement n'est pas appliqué.
        <template v-if="align.auto_rename"> Le renommage automatique est activé.</template>
        <template v-else> Le renommage automatique est désactivé (Réglages, Jellyfin).</template>
      </p>
      <p class="hint" data-jellyfin-scan>{{ align.message }}</p>
      <p v-if="align.scanning" class="hint">Les étapes sont bloquées pendant l'analyse Jellyfin.</p>
      <p class="hint library-align-flow">
        <span :class="{ 'is-current': alignStep === 0 }">Aligner</span>
        <span aria-hidden="true"> · </span>
        <span :class="{ 'is-current': alignStep === 1 }">Ranger</span>
        <span aria-hidden="true"> · </span>
        <span :class="{ 'is-current': alignStep >= 2 }">Terminé</span>
      </p>
      <p class="actions align-steps">
        <button
          type="button"
          data-align-step="align"
          :class="{ primary: alignStep === 0 }"
          :disabled="busy || align.scanning"
          @click="applyAlignment"
        >
          Aligner
        </button>
        <button
          type="button"
          data-align-step="range"
          :class="{ primary: alignStep === 1, waiting: alignStep < 1 }"
          :disabled="busy || align.scanning || alignStep < 1"
          @click="applyJellyfinRename"
        >
          Ranger
        </button>
        <button
          type="button"
          data-align-step="done"
          :class="{ primary: alignStep >= 2, waiting: alignStep < 2 }"
          :disabled="busy || align.scanning || alignStep < 2"
          @click="finishAlignment"
        >
          Terminer l'alignement
        </button>
      </p>
      <p class="actions">
        <button type="button" :disabled="busy" @click="refreshJellyfin(false)">Actualiser</button>
        <button type="button" :disabled="busy || align.scanning" @click="refreshJellyfin(true)">Tout relire</button>
      </p>
      <p v-if="align.attaches.length" class="hint">Rattachements : {{ align.attaches.map((row) => row.title).join(" · ") }}</p>
      <p v-if="align.reidentifies.length" class="hint">
        Jellyfin est plus récent :
        {{ align.reidentifies.map((row) => `${row.title} (${row.from_tmdb} → ${row.to_tmdb})`).join(" · ") }}
      </p>
      <p v-if="align.merges.length" class="hint">
        Fusions : {{ align.merges.map((row) => row.title).join(" · ") }}
      </p>
      <p v-if="align.cleaned_after_watch?.length" class="hint">
        Fichiers nettoyés après visionnage :
        {{ align.cleaned_after_watch.map((row) => row.title).join(" · ") }}
      </p>
      <p v-if="align.orphans.length" class="hint">
        Sans élément Jellyfin : {{ align.orphans.map((row) => row.title).join(" · ") }}
      </p>
      <p v-if="align.holds.length" class="hint">
        MediaTracker est plus récent :
        {{ align.holds.map((row) => row.title).join(" · ") }}
      </p>
      <p v-if="align.renames.some((row) => row.blocked === 'Identifiants différents.')" class="hint">
        Identifiants différents : revue manuelle. Ces fichiers restent en place, y compris avec le renommage automatique.
      </p>
      <ul v-if="align.renames.length" class="entry-list">
        <li v-for="move in align.renames" :key="move.src">
          <div class="entry-main">
            <span class="entry-copy">
              {{ move.src }} → {{ move.dest }}
              <span v-if="move.blocked" class="hint">{{ move.blocked }}</span>
            </span>
          </div>
        </li>
      </ul>
      <p v-if="align.pushes.length" class="hint" data-jellyfin-pushes>
        Poussées vers Jellyfin :
        <template v-for="(row, index) in align.pushes" :key="`${row.tmdb_id}-${row.state}-${index}`">
          <template v-if="index"> · </template>
          <span :class="{ error: row.state === 'failed' }">{{ pushLabel(row) }}</span>
        </template>
      </p>
      </div>
    </section>

    <section
      v-if="incomplete && (incomplete.files.length || incomplete.auto_message || incompleteStep === 'done')"
      v-show="activeTab === 'nettoyage'"
      class="library-section panel stack"
      data-incomplete-panel
      data-library-section="incomplete"
    >
      <h2 class="library-section-heading">
        <button
          type="button"
          class="library-section-toggle"
          :aria-expanded="sectionExpanded('incomplete', Boolean(incomplete.files.length))"
          @click="toggleSection('incomplete', Boolean(incomplete.files.length))"
        >
          <span class="library-section-chevron" aria-hidden="true" />
          <span class="library-section-label">Téléchargements incomplets</span>
          <span class="library-section-count">{{ incomplete.files.length }}</span>
        </button>
      </h2>
      <div
        v-show="sectionExpanded('incomplete', Boolean(incomplete.files.length))"
        class="library-section-body stack"
      >
      <p v-if="incomplete.auto_message" class="hint" data-incomplete-auto>{{ incomplete.auto_message }}</p>
      <p v-if="incomplete.auto_enabled" class="hint">
        La liste est recalculée à chaque affichage. Le passage automatique, au plus une fois par heure,
        suit les mêmes règles que le bouton : orphelins déjà en bibliothèque, fichiers de 64 Ko ou moins
        et contrôles aria2 tout de suite ; les autres au bout de 3 jours. Un téléchargement en cours,
        ou un fichier encore ouvert par aria2, est laissé.
      </p>
      <p v-else-if="incompleteStep !== 'preview'" class="hint">
        Aperçu : rien n'est supprimé avant votre confirmation. Les fichiers vont à la corbeille, ils ne sont pas effacés.
      </p>
      <p v-if="incompleteStatus" class="hint success" data-incomplete-result>
        {{ incompleteStatus }}
        <button v-if="incompleteUndoIds.length" type="button" class="linkish" :disabled="busy" @click="undoIncompleteTrash">
          Annuler
        </button>
      </p>
      <template v-if="incompleteStep === 'preview'">
        <h3>Aperçu avant la corbeille</h3>
        <p class="hint">Rien n'a été déplacé. Vérifiez la liste puis cliquez sur « Appliquer ».</p>
        <p class="hint warn">
          {{ incompleteSelected.length }} fichier(s) · {{ formatBytes(incompleteSelectedBytes) }} seront déplacés vers la corbeille.
        </p>
        <ul class="entry-list">
          <li v-for="file in incompleteSelected" :key="file.path">
            <div class="entry-main">
              <span class="entry-copy">
                <strong>{{ file.name }}</strong>
                <span class="hint">{{ fileSizeLine(file.written_bytes, file.reserved_bytes) }}</span>
              </span>
            </div>
          </li>
        </ul>
        <p class="hint">
          Durée de conservation :
          <RouterLink to="/reglages/corbeille">Réglages > Corbeille</RouterLink>.
        </p>
        <p class="actions">
          <button type="button" class="danger" :disabled="busy" data-incomplete-apply @click="applyIncomplete">
            Appliquer : déplacer {{ incompleteSelected.length }} fichier(s)
          </button>
          <button type="button" :disabled="busy" @click="backToIncompleteList">Revenir à la liste</button>
        </p>
      </template>
      <template v-else-if="incompleteStep === 'done' && !incomplete.files.length">
        <h3>Rien à ranger</h3>
        <p class="hint">Les fichiers incomplets sélectionnés sont dans la corbeille.</p>
      </template>
      <template v-else-if="incomplete.files.length">
        <p class="hint" data-incomplete-total>
          {{ formatBytes(incomplete.total_written_bytes) }} · {{ incomplete.files.length }} fichier(s)
          <span class="hint">
            (dossier {{ incompleteFolderLine(incomplete) }}, comme sur la carte disque)
          </span>
        </p>
        <p class="actions">
          <button type="button" :disabled="incompleteBusy" @click="loadIncomplete">Relire les incomplets</button>
          <button type="button" :disabled="busy" @click="selectAllIncomplete">Tout sélectionner</button>
          <span class="hint">{{ incompleteSelection.length }} sélectionné(s)</span>
        </p>
        <ul class="entry-list" data-incomplete-list>
          <li v-for="file in incomplete.files" :key="file.path">
            <label class="check entry-main">
              <input
                type="checkbox"
                :checked="incompleteSelection.includes(file.path)"
                @change="toggleIncomplete(file.path)"
              />
              <span class="entry-copy">
                <strong>{{ file.name }}</strong>
                <span class="hint">{{ file.reason }}</span>
              </span>
            </label>
            <span class="hint incomplete-size">{{ fileSizeLine(file.written_bytes, file.reserved_bytes) }}</span>
            <span class="hint">{{ jobLine(file) }}</span>
          </li>
        </ul>
        <p class="actions">
          <button
            type="button"
            class="primary"
            :disabled="busy || !incompleteSelected.length"
            data-incomplete-preview
            @click="showIncompletePreview"
          >
            Aperçu avant la corbeille
          </button>
        </p>
      </template>
      </div>
    </section>

    <section
      v-if="regroup && (regroup.groups.length || regroup.loose?.length || regroup.unmatched.length || regroup.ambiguous.length)"
      v-show="activeTab === 'a-traiter'"
      class="library-section panel stack"
      data-library-section="regroup"
    >
      <h2 class="library-section-heading">
        <button
          type="button"
          class="library-section-toggle"
          :aria-expanded="sectionExpanded('regroup', false)"
          @click="toggleSection('regroup', false)"
        >
          <span class="library-section-chevron" aria-hidden="true" />
          <span class="library-section-label">Dossiers hors norme</span>
          <span class="library-section-count">{{ regroupCount }}</span>
        </button>
      </h2>
      <div v-show="sectionExpanded('regroup', false)" class="library-section-body stack">
      <p class="hint">
        Aperçu, rien n'est déplacé tant que vous n'appliquez pas. Les fichiers prennent le nom
        Title (Year) - SxxExx. Quand deux fichiers sont le même épisode, la copie déjà normée est
        gardée et l'autre part à la corbeille. Elle n'est pas effacée.
      </p>
      <p v-if="regroup.groups.length" class="hint">{{ regroupSummary(regroup.groups) }}</p>
      <article v-for="group in regroup.groups" :key="group.media_id" class="stack">
        <strong>
          <router-link :to="`/medias/${group.media_id}`">{{ cleanDisplayTitle(group.title) }}<template v-if="group.year"> ({{ group.year }})</template></router-link>
        </strong>
        <span class="hint">
          {{ matchLabel(group.match) }}
          <template v-if="group.tmdb_id">
            · <a class="tmdb-link" :href="tmdbPage(group.tmdb_id, group.media_type)" target="_blank" rel="noreferrer">TMDB</a>
          </template>
          · {{ group.canonical }}
        </span>
        <ul class="entry-list">
          <li v-for="move in group.moves" :key="move.src">
            <div class="entry-main">
              <span class="entry-copy">
                <strong>{{ move.name }}</strong>
                <span v-if="move.original_name && move.original_name !== move.name" class="hint release-name">{{ move.original_name }}</span>
                <span class="hint">
                  {{ formatBytes(move.size || 0) }}
                  <template v-if="move.other_size != null"> · autre copie {{ formatBytes(move.other_size) }}</template>
                  · {{ move.blocked || (move.action === "trash" ? "Vers la corbeille" : move.dest) }}
                </span>
                <span v-if="move.warning" class="hint">{{ move.warning }}</span>
              </span>
            </div>
          </li>
        </ul>
        <p v-if="group.empty_after.length" class="hint">Vide ensuite : {{ group.empty_after.join(" · ") }}</p>
        <p class="actions">
          <button type="button" class="primary" :disabled="busy || !openMoves(group).length" @click="applyRegroup([group])">
            Regrouper cette fiche
          </button>
        </p>
      </article>
      <p v-if="regroup.groups.length" class="actions">
        <button
          type="button"
          class="primary"
          :disabled="busy || !regroup.groups.some((group) => openMoves(group).length)"
          @click="applyRegroup(regroup.groups, Boolean(regroup.loose?.length))"
        >
          Tout regrouper
        </button>
      </p>
      <template v-if="regroup.loose?.length">
        <h3>Sans fiche</h3>
        <p class="hint">
          Le même épisode existe dans des dossiers sans fiche. La copie déjà normée reste, l'autre est proposée à la corbeille.
        </p>
        <article v-for="(group, index) in regroup.loose" :key="`loose-${index}`" class="stack">
          <strong>{{ cleanDisplayTitle(group.title) }}</strong>
          <span class="hint">{{ group.canonical }}</span>
          <ul class="entry-list">
            <li v-for="move in group.moves" :key="move.src">
              <div class="entry-main">
                <span class="entry-copy">
                  <strong>{{ move.name }}</strong>
                  <span class="hint">
                    {{ formatBytes(move.size || 0) }}
                    <template v-if="move.other_size != null"> · copie gardée {{ formatBytes(move.other_size) }}</template>
                    · Vers la corbeille
                  </span>
                  <span v-if="move.warning" class="hint">{{ move.warning }}</span>
                </span>
              </div>
            </li>
          </ul>
        </article>
        <p class="actions">
          <button
            type="button"
            class="primary"
            :disabled="busy || !regroup.loose.some((group) => openMoves(group).length)"
            @click="applyRegroup(regroup.loose, true)"
          >
            Mettre les copies non normées à la corbeille
          </button>
        </p>
      </template>
      <p v-if="regroup.ambiguous.length" class="hint">Ambigu, rien n'est proposé : {{ regroup.ambiguous.join(" · ") }}</p>
      <p v-if="regroup.unmatched.length" class="hint">Sans fiche suivie : {{ regroup.unmatched.join(" · ") }}</p>
      <p v-if="regroup.empty.length" class="hint">Dossiers vides : {{ regroup.empty.join(" · ") }}</p>
      </div>
    </section>

    <section
      v-for="block in problemSections"
      v-show="activeTab === 'a-traiter'"
      :key="block.id"
      class="library-section panel stack"
      :data-library-section="block.id"
    >
      <h2 class="library-section-heading">
        <button
          type="button"
          class="library-section-toggle"
          :aria-expanded="sectionExpanded(block.id, true)"
          @click="toggleSection(block.id, true)"
        >
          <span class="library-section-chevron" aria-hidden="true" />
          <span class="library-section-label">{{ block.label }}</span>
          <span class="library-section-count">{{ block.groups.length }}</span>
        </button>
      </h2>
      <div v-show="sectionExpanded(block.id, true)" class="library-section-body stack">
    <article v-for="group in block.groups" :key="group.key" class="panel stack library-problem-card">
      <div class="entry-row">
        <div class="entry-main">
          <button type="button" class="entry-poster-button" @click="openSeries(group)">
            <TmdbPosterImg v-if="group.poster_url" class="entry-poster" :url="group.poster_url" thumb />
            <span v-else class="entry-poster entry-poster-empty" aria-hidden="true"></span>
          </button>
          <span class="entry-copy">
            <strong>
              <router-link v-if="group.media_id != null" :to="`/medias/${group.media_id}`">{{ cleanDisplayTitle(group.title) }}<template v-if="group.year"> ({{ group.year }})</template></router-link>
              <template v-else>{{ cleanDisplayTitle(group.title) }}<template v-if="group.year"> ({{ group.year }})</template></template>
            </strong>
            <span class="hint icon-line">
              {{ mediaKind(group.media_type) }}
              <template v-if="group.tmdb_id">
                · <a class="tmdb-link" :href="tmdbPage(group.tmdb_id, group.media_type)" target="_blank" rel="noreferrer">TMDB</a>
              </template>
              · {{ group.folders.length }} dossier(s)
              <StatusIcon v-if="group.duplicate || group.problem === 'duplicate'" name="missing" label="Dossiers en double" />
              <StatusIcon v-else-if="group.problem === 'unmatched'" name="missing" label="Non reconnu" />
              <StatusIcon v-else-if="group.problem === 'mismatch'" name="error" label="Fichier d'une autre série" />
            </span>
          </span>
        </div>
        <div class="entry-actions">
          <router-link v-if="group.media_id != null" class="button" :to="`/medias/${group.media_id}`">Fiche</router-link>
          <button
            v-if="group.folders[0]"
            type="button"
            :disabled="busy"
            @click="followFolder(group.folders.find((folder) => folder.canonical) || group.folders[0], !group.followed)"
          >
            {{ group.followed ? "Ne pas suivre" : "Suivre" }}
          </button>
        </div>
      </div>

      <ul class="entry-list">
        <li v-for="folder in group.folders" :key="folder.path">
          <div class="entry-main">
            <span class="entry-copy">
              <strong>{{ folder.name }}</strong>
              <span class="hint">
                <template v-if="folder.canonical">Dossier de référence · </template>
                <template v-if="folder.seasons.length">{{ folder.seasons.map((season) => season.label).join(" · ") }}</template>
                <template v-else>Aucun épisode</template>
              </span>
            </span>
          </div>
        </li>
      </ul>

      <p v-if="group.merge_hint" class="hint">{{ group.merge_hint }}</p>
      <template v-if="group.merges.length">
        <ul class="entry-list">
          <li v-for="move in group.merges" :key="move.src">
            <div class="entry-main">
              <span class="entry-copy">
                <strong>{{ move.name }}</strong>
                <span class="hint">{{ move.blocked || move.dest }}</span>
              </span>
            </div>
          </li>
        </ul>
        <p class="actions">
          <button type="button" class="primary" :disabled="busy || !group.merges.some((move) => !move.blocked)" @click="applyMerge(group)">
            Appliquer le regroupement
          </button>
        </p>
      </template>
      <p v-if="group.empty_after?.length" class="hint">
        Vide ensuite : {{ group.empty_after.join(" · ") }}. Le dossier sera proposé au nettoyage.
      </p>

      <ul v-if="group.files.length" class="entry-list">
        <li v-for="file in group.files" :key="file.path">
          <div class="entry-main">
            <span class="entry-copy">
              <strong>{{ file.name }}</strong>
              <span v-if="file.original_name && file.original_name !== file.name" class="hint release-name">{{ file.original_name }}</span>
              <span class="hint">{{ kindLabel(file.kind) }} · {{ file.reason }}</span>
              <span v-if="file.action === 'trash'" class="hint">
                {{ formatBytes(file.size || 0) }}
                <template v-if="file.other_size != null"> · copie gardée {{ formatBytes(file.other_size) }}</template>
              </span>
              <span v-if="file.warning" class="hint">À décider · {{ file.warning }}</span>
              <span v-if="file.action !== 'trash' && file.target_name" class="hint">Proposition : {{ file.target_name }}</span>
              <span v-if="file.blocked" class="hint">{{ file.blocked }}</span>
            </span>
          </div>
          <div class="entry-actions">
            <button
              v-if="file.action === 'trash' && !file.blocked"
              type="button"
              :disabled="busy"
              @click="applyFiles([file.path], true, [file])"
            >
              Mettre à la corbeille
            </button>
            <button v-else-if="file.target_path && !file.blocked" type="button" :disabled="busy" @click="applyFiles([file.path])">
              Appliquer
            </button>
          </div>
        </li>
      </ul>
      <p v-if="trashMoves(group).length > 1" class="actions">
        <button type="button" :disabled="busy" @click="applyFiles(trashMoves(group).map((file) => file.path), true, trashMoves(group))">
          Mettre ces copies à la corbeille
        </button>
      </p>
      <p v-if="fileMoves(group).length > 1" class="actions">
        <button type="button" :disabled="busy" @click="applyFiles(fileMoves(group).map((file) => file.path))">
          Appliquer les fichiers de cette série
        </button>
      </p>

      <template v-if="group.problem === 'unmatched'">
        <p class="actions">
          <button type="button" class="primary" :disabled="busy" @click="identify(group.folders[0])">Identifier</button>
        </p>
        <div v-if="manual && group.folders.some((folder) => folder.path === manual.path)" class="manual-panel">
          <h3>Identification manuelle</h3>
          <form class="inline" @submit.prevent="searchManual">
            <label>
              <span>Nom sur TMDB</span>
              <input v-model="manual.query" minlength="2" />
            </label>
            <label>
              <span>Type</span>
              <select v-model="manual.type">
                <option value="series">Série</option>
                <option value="film">Film</option>
              </select>
            </label>
            <button type="submit" class="primary" :disabled="manual.searching">Chercher</button>
            <a class="button-link" :href="tmdbSearch(manual.query)" target="_blank" rel="noreferrer">Chercher sur TMDB ↗</a>
          </form>
          <ul v-if="manual.results.length" class="entry-list">
            <li v-for="hit in manual.results" :key="`${hit.media_type}-${hit.tmdb_id}`">
              <a class="entry-main" :href="tmdbPage(hit.tmdb_id, hit.media_type)" target="_blank" rel="noreferrer">
                <TmdbPosterImg v-if="hit.poster_url" class="entry-poster" :url="hit.poster_url" thumb />
                <span v-else class="entry-poster entry-poster-empty" aria-hidden="true"></span>
                <span class="entry-copy">
                  <strong>{{ hit.title }}</strong>
                  <span class="hint">{{ candidateFacts(hit) }}</span>
                </span>
              </a>
              <div class="entry-actions">
                <button type="button" class="primary" :disabled="busy" @click="applyFolder(group.folders[0], hit.tmdb_id, hit.media_type)">Appliquer</button>
              </div>
            </li>
          </ul>
          <ul v-if="suggestions[group.folders[0].path]?.length" class="entry-list">
            <li v-for="hint in suggestions[group.folders[0].path]" :key="hint.tmdb_id">
              <div class="entry-main">
                <TmdbPosterImg v-if="hint.poster_url" class="entry-poster" :url="hint.poster_url" thumb />
                <span v-else class="entry-poster entry-poster-empty" aria-hidden="true"></span>
                <span class="entry-copy">
                  <strong>{{ hint.title }}</strong>
                  <span class="hint">{{ candidateFacts({ ...hint, media_type: hint.media_type || group.media_type }) }}</span>
                </span>
              </div>
              <div class="entry-actions">
                <button type="button" class="primary" @click="applyFolder(group.folders[0], hint.tmdb_id, hint.media_type || group.media_type)">Choisir</button>
              </div>
            </li>
          </ul>
          <form class="inline" @submit.prevent="previewManual">
            <label>
              <span>Identifiant TMDB</span>
              <input v-model="manual.tmdbId" inputmode="numeric" placeholder="900002" />
            </label>
            <button type="submit">Aperçu</button>
          </form>
          <div v-if="manual.preview" class="entry-row">
            <div class="entry-main">
              <TmdbPosterImg v-if="manual.preview.poster_url" class="entry-poster" :url="manual.preview.poster_url" thumb />
              <span v-else class="entry-poster entry-poster-empty" aria-hidden="true"></span>
              <span class="entry-copy">
                <strong>{{ manual.preview.title }}<template v-if="manual.preview.year"> ({{ manual.preview.year }})</template></strong>
                <span class="hint">{{ candidateFacts(manual.preview) }}</span>
              </span>
            </div>
            <div class="entry-actions">
              <button type="button" class="primary" :disabled="busy" @click="applyFolder(group.folders[0], manual.preview.tmdb_id, manual.preview.media_type)">
                Appliquer à tout le dossier
              </button>
            </div>
          </div>
        </div>
      </template>
    </article>
      </div>
    </section>

    <p v-if="renameId != null && activeTab === 'a-traiter'" class="actions">
      <button type="button" :disabled="busy" @click="undoRename">Annuler le dernier déplacement</button>
    </p>

    <div v-show="activeTab === 'contenu'" class="library-tab-panel stack" data-library-tab-panel="contenu">
      <p class="hint library-content-head">
        <strong>{{ calmFilms.length }} Films OK</strong>
        <span aria-hidden="true"> · </span>
        <strong>{{ calmSeries.length }} Séries OK</strong>
      </p>
      <p class="hint">
        États de suivi (disponible, en attente, en pause) :
        <RouterLink to="/medias">page Médias</RouterLink>.
      </p>
      <p class="actions library-calm-filters">
        <button type="button" :class="{ active: contentFilter === 'all' }" @click="contentFilter = 'all'">
          Tous ({{ countContentForFilter('all', [...calmFilms, ...calmSeries]) }})
        </button>
        <button type="button" :class="{ active: contentFilter === 'verified' }" @click="contentFilter = 'verified'">
          Vérifiés ({{ countContentForFilter('verified', [...calmFilms, ...calmSeries]) }})
        </button>
        <button type="button" :class="{ active: contentFilter === 'missing' }" @click="contentFilter = 'missing'">
          Fichier manquant ({{ countContentForFilter('missing', [...calmFilms, ...calmSeries]) }})
        </button>
        <button type="button" :class="{ active: contentFilter === 'unidentified' }" @click="contentFilter = 'unidentified'">
          Non identifié ({{ countContentForFilter('unidentified', [...calmFilms, ...calmSeries]) }})
        </button>
      </p>
      <form class="filters library-calm-toolbar" @submit.prevent>
        <label>
          <span>Recherche</span>
          <input v-model="contentQuery" type="search" placeholder="Titre ou dossier" />
        </label>
        <label>
          <span>Tri</span>
          <select v-model="contentSort">
            <option value="alpha">Alphabétique</option>
            <option value="added">Date d'ajout</option>
          </select>
        </label>
      </form>
      <section
        v-for="block in contentSections"
        :key="block.id"
        class="library-section panel stack library-calm-section"
        :data-library-section="block.id"
      >
        <h2 class="library-section-heading">
          <button
            type="button"
            class="library-section-toggle"
            :aria-expanded="sectionExpanded(block.id, true)"
            @click="toggleSection(block.id, true)"
          >
            <span class="library-section-chevron" aria-hidden="true" />
            <span class="library-section-label">{{ block.label }}</span>
            <span class="library-section-count">{{ block.groups.length }}</span>
          </button>
        </h2>
        <div v-show="sectionExpanded(block.id, true)" class="library-section-body stack">
          <p v-if="!visibleContentGroups(block.groups).length" class="hint">{{ block.emptyFilter }}</p>
          <ul v-else class="entry-list library-content-folders">
            <template v-for="group in visibleContentGroups(block.groups)" :key="group.key">
              <li v-for="folder in group.folders" :key="folder.path" class="library-content-row">
                <div class="entry-main">
                  <span class="entry-copy">
                    <strong>
                      <router-link v-if="group.media_id != null" :to="`/medias/${group.media_id}`">
                        {{ cleanDisplayTitle(group.title) || group.title }}
                      </router-link>
                      <template v-else>{{ cleanDisplayTitle(group.title) || group.title }}</template>
                    </strong>
                    <span class="hint">{{ folder.path }}</span>
                  </span>
                </div>
                <div v-if="group.media_id != null" class="entry-actions">
                  <router-link class="button" :to="`/medias/${group.media_id}`">Fiche</router-link>
                </div>
              </li>
            </template>
          </ul>
        </div>
      </section>
    </div>

    <section v-if="review" v-show="activeTab === 'nettoyage'" class="library-section panel stack" data-library-section="cleanup">
      <h2 class="library-section-heading">
        <button
          type="button"
          class="library-section-toggle"
          :aria-expanded="sectionExpanded('cleanup', false)"
          @click="toggleSection('cleanup', false)"
        >
          <span class="library-section-chevron" aria-hidden="true" />
          <span class="library-section-label">Dossiers vides</span>
          <span class="library-section-count">{{ cleanupCount }}</span>
        </button>
      </h2>
      <div v-show="sectionExpanded('cleanup', false)" class="library-section-body stack">
      <p class="hint">
        Un dossier sans vidéo est effectivement vide, même s'il reste des affiches, un logo ou des .nfo.
        Une miniature ou un .nfo dont la vidéo a disparu est proposé aussi. Les fichiers vont à la corbeille.
      </p>
      <p v-if="!review.empty.length" class="hint">Aucune suggestion.</p>
      <ul v-else class="entry-list">
        <li v-for="folder in review.empty" :key="folder.path">
          <div class="entry-main">
            <span class="entry-copy">
              <strong>{{ folder.path }}</strong>
              <span class="hint">{{ folder.summary }} · {{ formatBytes(folder.size) }}</span>
            </span>
          </div>
          <div class="entry-actions">
            <button type="button" class="danger" :disabled="busy" @click="removeOne(folder)">Corbeille</button>
          </div>
        </li>
      </ul>
      <p v-if="review.empty.length" class="actions">
        <button type="button" class="danger" :disabled="busy" @click="cleanEmpty">Tout mettre à la corbeille</button>
      </p>
      </div>
    </section>
    <MediaQuickView :target="quick" :follow="quickFollow" @close="quick = null" @changed="analyze" />
  </section>
</template>
